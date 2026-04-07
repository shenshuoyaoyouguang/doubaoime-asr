from __future__ import annotations

from typing import Any

from .config import AgentConfig
from .injection_manager import TextInjectionManager
from .overlay_preview import OverlayPreview
from .overlay_scheduler import OverlayRenderScheduler
from .text_polisher import TextPolisher
from .win_audio_output import SystemOutputMuteGuard
from .stable_simple_app_compat import _SchedulerCompat


def bootstrap_runtime_services(
    app: Any,
    module: Any,
    config: AgentConfig,
) -> _SchedulerCompat:
    """按 stable_simple_app 模块级 monkeypatch 结果重建运行时服务。"""
    coordinator = app._coordinator
    logger = coordinator.logger

    injection_manager_cls = getattr(module, "TextInjectionManager", TextInjectionManager)
    coordinator.injection_service._manager = injection_manager_cls(
        logger,
        policy=config.injection_policy,
    )

    preview_cls = getattr(module, "OverlayPreview", OverlayPreview)
    scheduler_cls = getattr(module, "OverlayRenderScheduler", OverlayRenderScheduler)
    overlay_service = coordinator.overlay_service
    preview = preview_cls(logger=logger, config=config)
    raw_scheduler = scheduler_cls(
        preview,
        logger=logger,
        fps=config.overlay_render_fps,
    )
    scheduler_compat = _SchedulerCompat(raw_scheduler, raw_scheduler=True)
    overlay_service.install_runtime_components(preview, scheduler_compat)

    polisher_cls = getattr(module, "TextPolisher", TextPolisher)
    coordinator.text_polisher = polisher_cls(logger, config)

    mute_guard_cls = getattr(module, "SystemOutputMuteGuard", SystemOutputMuteGuard)
    coordinator.capture_output_guard = mute_guard_cls(
        logger,
        policy=config.capture_output_policy,
    )
    return scheduler_compat


def capture_runtime_impls(app: Any) -> None:
    """缓存运行态原始实现，供 compat 分支按需回落。"""
    coordinator = app._coordinator
    injection_service = coordinator.injection_service
    app._runtime_inject_final_impl = coordinator._inject_final
    app._runtime_apply_inline_interim_impl = injection_service.apply_inline_interim
    app._runtime_prepare_final_text_impl = injection_service.prepare_final_text
    app._runtime_clear_active_session_impl = coordinator._clear_active_session
    app._runtime_send_stop_if_needed_impl = coordinator._send_stop_if_needed


def bind_runtime_compat_hooks(app: Any) -> None:
    """将 coordinator 关键回调重新绑定到 app facade，保留 monkeypatch 能力。"""
    coordinator = app._coordinator
    injection_service = coordinator.injection_service

    async def _coord_inject_final(text: str) -> None:
        await app._inject_final(text)

    async def _coord_apply_inline_interim(text: str) -> None:
        await app._apply_inline_interim(text)

    async def _coord_prepare_final_text(text: str) -> None:
        await app._prepare_final_text_compat(text)

    async def _coord_clear_active_session() -> None:
        await app._clear_active_session()

    async def _coord_send_stop_if_needed() -> None:
        await app._send_stop_if_needed()

    async def _coord_handle_restart_as_admin() -> None:
        await app._handle_restart_as_admin()

    coordinator._inject_final = _coord_inject_final
    injection_service.apply_inline_interim = _coord_apply_inline_interim
    injection_service.prepare_final_text = _coord_prepare_final_text
    coordinator._clear_active_session = _coord_clear_active_session
    coordinator._send_stop_if_needed = _coord_send_stop_if_needed
    coordinator._handle_restart_as_admin = _coord_handle_restart_as_admin


__all__ = [
    "bind_runtime_compat_hooks",
    "bootstrap_runtime_services",
    "capture_runtime_impls",
]
