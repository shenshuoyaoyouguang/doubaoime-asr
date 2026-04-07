from __future__ import annotations

from typing import Any

from .stable_simple_app_compat import _SessionCompat, _SessionCompatWrapper


def has_test_session_override(test_session: Any, unset: object) -> bool:
    """判断当前是否存在显式测试会话覆盖。"""
    return test_session is not unset


def reset_test_session_override(unset: object) -> object:
    """重置测试会话覆盖哨兵。"""
    return unset


def wrap_session(session: Any) -> _SessionCompatWrapper:
    """将运行态 session 包装为 compat wrapper。"""
    return _SessionCompat.wrap(session)


def uses_runtime_session_flow(
    *,
    test_session: Any,
    unset: object,
    session: Any,
) -> bool:
    """判断当前是否应走 runtime session flow。"""
    return test_session is unset and isinstance(session, _SessionCompatWrapper)


__all__ = [
    "has_test_session_override",
    "reset_test_session_override",
    "uses_runtime_session_flow",
    "wrap_session",
]
