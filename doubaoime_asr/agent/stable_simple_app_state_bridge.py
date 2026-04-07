from __future__ import annotations

from typing import Any

from .stable_simple_app_compat import _InjectionManagerCompat, _PreviewCompat


def get_coordinator_attr(app: Any, name: str) -> Any:
    """读取 coordinator 上的属性。"""
    return getattr(app._coordinator, name)


def set_coordinator_attr(app: Any, name: str, value: Any) -> None:
    """写入 coordinator 上的属性。"""
    setattr(app._coordinator, name, value)


def get_status(app: Any) -> str:
    """读取当前状态文案。"""
    return app._coordinator.get_status()


def set_status(app: Any, value: str) -> None:
    """设置当前状态文案。"""
    app._coordinator.set_status(value)


def get_injection_manager(app: Any) -> Any:
    """获取 compat injection manager 单例。"""
    if app._injection_manager_compat is None:
        app._injection_manager_compat = _InjectionManagerCompat(
            app._coordinator.injection_service
        )
    return app._injection_manager_compat


def get_preview(app: Any) -> Any:
    """获取 compat preview 单例。"""
    if app._preview_compat is None:
        app._preview_compat = _PreviewCompat(app._coordinator.overlay_service)
    return app._preview_compat


def get_overlay_scheduler(app: Any) -> Any:
    """获取 compat overlay scheduler。"""
    return app._overlay_scheduler_compat


def get_text_polisher(app: Any) -> Any:
    """获取 text polisher。"""
    return app._coordinator.text_polisher


def get_capture_output_guard(app: Any) -> Any:
    """获取 capture output guard。"""
    return app._coordinator.capture_output_guard


__all__ = [
    "get_capture_output_guard",
    "get_coordinator_attr",
    "get_injection_manager",
    "get_overlay_scheduler",
    "get_preview",
    "get_status",
    "get_text_polisher",
    "set_coordinator_attr",
    "set_status",
]
