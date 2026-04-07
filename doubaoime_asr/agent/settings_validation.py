from __future__ import annotations

from .settings_schema import FIELD_LABELS, PAGE_LABELS


class SettingsValidationError(ValueError):
    """设置值非法。"""

    def __init__(self, message: str, *, field_name: str | None = None) -> None:
        super().__init__(message)
        self.field_name = field_name


def validation_banner_message(exc: SettingsValidationError) -> str:
    field_label = FIELD_LABELS.get(exc.field_name or "", "")
    if field_label:
        return f"请检查「{field_label}」：{exc}"
    return f"请检查输入：{exc}"


def inline_error_message(exc: SettingsValidationError) -> str:
    return str(exc)


def restore_banner_message(page_name: str) -> str:
    page_label = PAGE_LABELS.get(page_name, page_name)
    return f"已恢复「{page_label}」默认值，记得点保存。"


def preview_banner_message() -> str:
    return "已发送浮层预览；此操作不会保存设置。"
