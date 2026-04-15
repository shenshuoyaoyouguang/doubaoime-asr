"""
Coordinator Config Runtime 模块。

提供配置应用与回滚功能。
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import threading
from typing import TYPE_CHECKING

from .config import AgentConfig
from .config_update_plan import build_config_update_plan, polisher_config_changed
from .events import ConfigChangeEvent

if TYPE_CHECKING:
    from .coordinator import VoiceInputCoordinator


__all__ = [
    "apply_config",
    "polisher_config_changed_wrapper",
    "preview_settings_overlay",
    "run_preview_overlay",
]


def preview_settings_overlay(coordinator: "VoiceInputCoordinator", config: AgentConfig) -> None:
    """在设置页预览浮层样式。"""
    if coordinator._loop is None:
        return
    if coordinator.session_manager.is_streaming():
        coordinator.set_status("录音中，暂不预览浮层")
        return
    with coordinator._preview_lock:
        coordinator._preview_counter += 1
        preview_id = coordinator._preview_counter
    coordinator._emit_threadsafe(
        coordinator._loop,
        ConfigChangeEvent(config=config, preview_id=preview_id, preview_only=True),
    )


async def run_preview_overlay(coordinator: "VoiceInputCoordinator", config: AgentConfig, *, preview_id: int) -> None:
    """应用临时配置并显示短暂预览。"""
    coordinator.overlay_service.configure(config)
    try:
        await coordinator.overlay_service.show_microphone("浮层预览：请确认字号、宽度与透明度")
        await asyncio.sleep(1.2)
    finally:
        with coordinator._preview_lock:
            still_latest = preview_id == coordinator._preview_counter
        if still_latest:
            await coordinator.overlay_service.hide("settings_preview")
            coordinator.overlay_service.configure(coordinator.config)


async def apply_config(coordinator: "VoiceInputCoordinator", new_config: AgentConfig, logger: logging.Logger) -> None:
    """应用新配置。"""
    old_config = coordinator.config
    if old_config.credential_path != new_config.credential_path:
        coordinator._asr_preflight.invalidate()
    old_mode = coordinator.mode
    old_pending_listener_rebind = coordinator._pending_listener_rebind
    old_pending_worker_restart = coordinator._pending_worker_restart
    old_pending_polisher_warmup = coordinator._pending_polisher_warmup

    update_plan = build_config_update_plan(old_config, new_config)
    hotkey_changed = update_plan.hotkey_changed
    worker_changed = update_plan.worker_changed
    polisher_changed = update_plan.polisher_changed
    session_active = coordinator.session_manager.is_streaming()
    listener_rebound = False
    worker_restarted = False

    try:
        coordinator.config = new_config
        coordinator.mode = new_config.mode

        # 更新各 Service
        coordinator.session_manager.config = new_config
        coordinator.overlay_service.configure(new_config)
        coordinator.injection_service.configure(new_config)
        coordinator.text_polisher.configure(new_config)
        coordinator.capture_output_guard.configure(new_config.capture_output_policy)

        if hotkey_changed:
            if session_active:
                coordinator._pending_listener_rebind = True
            else:
                coordinator.hotkey_service.update_hotkey(new_config.effective_hotkey_vk())
                listener_rebound = True

        if worker_changed:
            if session_active:
                coordinator._pending_worker_restart = True
            else:
                await coordinator.session_manager.restart_worker()
                worker_restarted = True

        if polisher_changed:
            if session_active:
                coordinator._pending_polisher_warmup = True
            else:
                _schedule_polisher_warmup(coordinator, "config_update")

        coordinator.config.save()
    except Exception:
        logger.exception("apply_config_failed")
        # 回滚
        coordinator.config = old_config
        coordinator.mode = old_mode
        coordinator._pending_listener_rebind = old_pending_listener_rebind
        coordinator._pending_worker_restart = old_pending_worker_restart
        coordinator._pending_polisher_warmup = old_pending_polisher_warmup
        coordinator.session_manager.config = old_config
        coordinator.overlay_service.configure(old_config)
        coordinator.injection_service.configure(old_config)
        coordinator.text_polisher.configure(old_config)
        coordinator.capture_output_guard.configure(old_config.capture_output_policy)
        if listener_rebound:
            coordinator.hotkey_service.update_hotkey(old_config.effective_hotkey_vk())
        if worker_restarted:
            await coordinator.session_manager.restart_worker()
        with contextlib.suppress(Exception):
            coordinator.config.save()
        coordinator.set_status("设置保存失败，已恢复旧配置")
        return

    if coordinator._tray_icon is not None:
        with contextlib.suppress(Exception):
            coordinator._tray_icon.update_menu()

    if not session_active:
        if hotkey_changed:
            coordinator.set_status(f"热键已更新为 {new_config.effective_hotkey_display()}")
        elif worker_changed:
            coordinator.set_status("设置已保存并重启识别服务")
        elif polisher_changed:
            coordinator.set_status("设置已保存并更新润色配置")
        else:
            coordinator.set_status("设置已保存")
    else:
        logger.info("settings_saved_during_active_session")


def _schedule_polisher_warmup(coordinator: "VoiceInputCoordinator", reason: str) -> None:
    """调度润色预热。"""
    if coordinator._loop is None:
        return
    if coordinator._polisher_warmup_task is not None and not coordinator._polisher_warmup_task.done():
        coordinator._polisher_warmup_task.cancel()
    # 动态导入避免循环依赖
    from .config import POLISH_MODE_OLLAMA

    if coordinator.config.polish_mode != POLISH_MODE_OLLAMA or not coordinator.config.ollama_warmup_enabled:
        coordinator._polisher_warmup_task = None
        return
    coordinator._polisher_warmup_task = asyncio.create_task(
        _run_polisher_warmup(coordinator, reason),
        name="doubao-polisher-warmup",
    )


async def _run_polisher_warmup(coordinator: "VoiceInputCoordinator", reason: str) -> None:
    """执行润色预热。"""
    try:
        warmed = await coordinator.text_polisher.warmup()
        coordinator.logger.info("text_polisher_warmup_finished reason=%s warmed=%s", reason, warmed)
    except asyncio.CancelledError:
        coordinator.logger.info("text_polisher_warmup_cancelled reason=%s", reason)
        raise
    except Exception:
        coordinator.logger.exception("text_polisher_warmup_failed reason=%s", reason)
    finally:
        current_task = asyncio.current_task()
        if coordinator._polisher_warmup_task is current_task:
            coordinator._polisher_warmup_task = None


def polisher_config_changed_wrapper(coordinator: "VoiceInputCoordinator", old_config: AgentConfig, new_config: AgentConfig) -> bool:
    """检查润色配置是否变化。"""
    return polisher_config_changed(old_config, new_config)