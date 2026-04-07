from __future__ import annotations

from typing import Any


def activate_capture_output(app: Any) -> str | None:
    """启用 capture output。"""
    return app._coordinator._activate_capture_output()


def release_capture_output(app: Any) -> str | None:
    """释放 capture output。"""
    return app._coordinator._release_capture_output()


def mode_display_label(app: Any, mode: Any = None) -> str:
    """获取模式显示文案。"""
    return app._coordinator._mode_display_label(mode)


def session_start_status(app: Any, capture_output_warning: str | None) -> str:
    """获取会话启动状态文案。"""
    return app._coordinator._session_start_status(capture_output_warning)


def status_for_final_result(app: Any, result: Any, raw_text: str) -> str:
    """获取最终结果状态文案。"""
    return app._coordinator._status_for_final_result(result, raw_text)


async def resolve_final_text(app: Any, raw_text: str) -> Any:
    """解析最终文本。"""
    return await app._coordinator._resolve_final_text(raw_text)


def polisher_config_changed(app: Any, old_config: Any, new_config: Any) -> bool:
    """判断 polisher 配置是否变化。"""
    return app._coordinator._polisher_config_changed(old_config, new_config)


__all__ = [
    "activate_capture_output",
    "mode_display_label",
    "polisher_config_changed",
    "release_capture_output",
    "resolve_final_text",
    "session_start_status",
    "status_for_final_result",
]
