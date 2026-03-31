"""
注入服务：封装文本注入逻辑，管理 CompositionSession，处理焦点变化和注入失败。

从 Controller 中提取的职责：
- 目标捕获与管理
- 注入策略执行
- 流式上屏支持
- 焦点变化异常处理
"""
from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import Literal

from .composition import CompositionSession
from .config import (
    AgentConfig,
    STREAMING_TEXT_MODE_SAFE_INLINE,
)
from .injection_manager import InjectionResult, TextInjectionManager
from .input_injector import FocusChangedError, FocusTarget


Mode = Literal["recognize", "inject"]


@dataclass(slots=True)
class InjectionSessionState:
    """注入会话状态，用于跟踪当前会话的注入状态。"""

    target: FocusTarget | None = None
    mode: Mode = "inject"
    composition: CompositionSession | None = None
    inline_streaming_enabled: bool = False
    final_injection_blocked: bool = False

    def begin(
        self,
        target: FocusTarget | None,
        mode: Mode,
        *,
        composition: CompositionSession | None = None,
        inline_streaming_enabled: bool = False,
    ) -> None:
        """开始新的注入会话。"""
        self.target = target
        self.mode = mode
        self.composition = composition
        self.inline_streaming_enabled = inline_streaming_enabled
        self.final_injection_blocked = False

    def clear(self) -> None:
        """清除会话状态。"""
        self.target = None
        self.mode = "inject"
        self.composition = None
        self.inline_streaming_enabled = False
        self.final_injection_blocked = False

    def block_injection(self) -> None:
        """标记注入被阻止。"""
        self.inline_streaming_enabled = False
        self.final_injection_blocked = True
        self.target = None


class InjectionService:
    """
    注入服务：封装文本注入逻辑。

    主要职责：
    1. 目标捕获与管理
    2. 注入策略执行（直接注入、剪贴板回退）
    3. 流式上屏支持（CompositionSession 管理）
    4. 焦点变化异常处理
    """

    def __init__(self, logger: logging.Logger, config: AgentConfig) -> None:
        self._logger = logger
        self._config = config
        self._manager = TextInjectionManager(logger, policy=config.injection_policy)
        self._session = InjectionSessionState()
        self._process_elevated = False

    # ===== 目标管理 =====

    def capture_target(self) -> FocusTarget | None:
        """捕获当前前景窗口作为注入目标。"""
        return self._manager.capture_target()

    def get_current_target(self) -> FocusTarget | None:
        """获取当前会话的注入目标。"""
        return self._session.target

    def get_current_mode(self) -> Mode:
        """获取当前会话的注入模式。"""
        return self._session.mode

    # ===== 会话管理 =====

    def begin_session(
        self,
        target: FocusTarget | None,
        mode: Mode,
        *,
        inline_streaming_enabled: bool = False,
    ) -> CompositionSession | None:
        """
        开始注入会话。

        如果启用流式上屏，创建 CompositionSession 并返回。
        """
        composition: CompositionSession | None = None
        if inline_streaming_enabled and target is not None:
            composition = CompositionSession(self._manager.injector, target)

        self._session.begin(
            target,
            mode,
            composition=composition,
            inline_streaming_enabled=inline_streaming_enabled,
        )
        return composition

    def end_session(self) -> None:
        """结束注入会话。"""
        self._session.clear()

    def get_composition(self) -> CompositionSession | None:
        """获取当前的 CompositionSession。"""
        return self._session.composition

    # ===== 注入执行 =====

    async def inject_final(self, text: str) -> InjectionResult | None:
        """
        执行最终文本注入。

        返回 InjectionResult 表示注入成功，返回 None 表示注入被跳过或阻止。
        可能抛出 FocusChangedError。
        """
        if self._session.mode != "inject":
            if text:
                self._logger.info("inject_skipped reason=recognize_mode text_length=%s", len(text))
            return None

        if self._session.target is None or self._session.final_injection_blocked:
            return None

        # 流式上屏模式：使用 CompositionSession
        if self._session.composition is not None and self._session.inline_streaming_enabled:
            return await self._inject_final_inline(text)

        # 检查管理员权限要求
        if self._target_requires_admin(self._session.target):
            self._logger.warning(
                "inject_blocked_elevated_target hwnd=%s pid=%s process=%s",
                self._session.target.hwnd,
                self._session.target.process_id,
                self._session.target.process_name,
            )
            return None

        # 直接注入
        return await self._inject_final_direct(text)

    async def _inject_final_inline(self, text: str) -> InjectionResult | None:
        """流式上屏模式的最终注入。"""
        composition = self._session.composition
        if composition is None:
            return None

        try:
            if composition.rendered_text == text and composition.final_text == text:
                self._logger.info("inject_success method=inline_composition_skipped")
                return InjectionResult(method="inline_composition_skipped")
            composition.finalize(text)
            self._logger.info("inject_success method=inline_composition")
            return InjectionResult(method="inline_composition")
        except FocusChangedError:
            self.handle_focus_changed()
            self._logger.warning("inject_focus_changed")
            raise
        except Exception:
            blocked = self._handle_inline_failure(
                log_tag="inject_inline_final_failed",
            )
            if blocked:
                return None
            # 回退到直接注入
            self._session.inline_streaming_enabled = False
            self._session.composition = None
            return await self.inject_final(text)

    async def _inject_final_direct(self, text: str) -> InjectionResult:
        """直接注入模式。"""
        target = self._session.target
        if target is None:
            raise RuntimeError("no target for direct injection")

        try:
            result = await self._manager.inject_text(target, text)
            self._logger.info(
                "inject_success method=%s target_profile=%s clipboard_touched=%s clipboard_restored=%s",
                result.method,
                result.target_profile,
                result.clipboard_touched,
                result.restored_clipboard,
            )
            return result
        except FocusChangedError:
            self._logger.warning("inject_focus_changed")
            raise

    # ===== 流式注入 =====

    def should_enable_inline_streaming(self, target: FocusTarget) -> bool:
        """
        判断是否应启用流式上屏。

        条件：
        1. 当前模式为 inject
        2. 配置的流式文本模式为 safe_inline
        3. 目标不是终端
        """
        return (
            self._session.mode == "inject"
            and self._config.streaming_text_mode == STREAMING_TEXT_MODE_SAFE_INLINE
            and not target.is_terminal
        )

    def is_inline_streaming_enabled(self) -> bool:
        """检查当前会话是否启用了流式上屏。"""
        return self._session.inline_streaming_enabled

    async def apply_inline_interim(self, text: str) -> None:
        """
        应用流式中间结果。

        使用 CompositionSession 进行实时上屏更新。
        """
        if not self._session.inline_streaming_enabled:
            return

        composition = self._session.composition
        if composition is None:
            return

        if composition.rendered_text == text:
            return

        try:
            composition.render_interim(text)
        except FocusChangedError:
            self.handle_focus_changed()
            self._logger.warning("inline_streaming_focus_changed")
        except Exception:
            self._handle_inline_failure(log_tag="inline_streaming_failed")

    async def prepare_final_text(self, text: str) -> None:
        """
        准备流式最终文本。

        在流式上屏模式下，提前准备最终文本以确保最终结果一致。
        """
        if not self._session.inline_streaming_enabled:
            return

        composition = self._session.composition
        if composition is None:
            return

        if composition.rendered_text == text and composition.final_text == text:
            return

        try:
            composition.finalize(text)
        except FocusChangedError:
            self.handle_focus_changed()
            self._logger.warning("inline_final_focus_changed")
        except Exception:
            self._handle_inline_failure(log_tag="inline_final_prepare_failed")

    # ===== 焦点变化处理 =====

    def handle_focus_changed(self) -> None:
        """
        处理焦点变化。

        禁用流式上屏并阻止后续注入。
        """
        self._session.block_injection()

    def is_injection_blocked(self) -> bool:
        """检查注入是否被阻止（焦点已变化）。"""
        return self._session.final_injection_blocked

    def _handle_inline_failure(
        self,
        *,
        log_tag: str,
    ) -> bool:
        """
        处理流式上屏失败。

        返回 True 表示已阻止注入（已有已渲染文本），False 表示可以回退。
        """
        composition = self._session.composition
        composed_text_exists = bool(
            composition is not None and (composition.rendered_text or composition.final_text)
        )

        if composed_text_exists:
            self._session.block_injection()
            self._logger.exception(log_tag)
            return True

        self._logger.exception(log_tag)
        return False

    # ===== 权限检查 =====

    def set_process_elevated(self, elevated: bool) -> None:
        """设置当前进程是否为管理员权限。"""
        self._process_elevated = elevated

    def target_requires_admin(self, target: FocusTarget | None) -> bool:
        """检查目标是否需要管理员权限。"""
        return self._target_requires_admin(target)

    def _target_requires_admin(self, target: FocusTarget | None) -> bool:
        """内部方法：检查目标是否需要管理员权限。"""
        return target is not None and target.is_elevated is True and not self._process_elevated

    # ===== 配置更新 =====

    def configure(self, config: AgentConfig) -> None:
        """更新配置。"""
        self._config = config
        self._manager.set_policy(config.injection_policy)

    def get_injection_policy(self) -> str:
        """获取当前的注入策略。"""
        return self._config.injection_policy

    def get_streaming_text_mode(self) -> str:
        """获取当前的流式文本模式。"""
        return self._config.streaming_text_mode