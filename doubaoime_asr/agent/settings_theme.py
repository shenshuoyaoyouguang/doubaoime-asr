from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SettingsPalette:
    app_background: str
    panel_background: str
    card_background: str
    surface_elevated: str
    surface_card: str
    border_subtle: str
    border_focus: str
    divider_subtle: str
    text_primary: str
    text_secondary: str
    text_muted: str
    accent_primary: str
    accent_pressed: str
    accent_soft: str
    status_success: str
    status_success_fill: str
    status_success_border: str
    status_error: str
    status_error_fill: str
    status_error_border: str
    status_info: str
    status_info_fill: str


@dataclass(frozen=True, slots=True)
class SettingsTypography:
    window_title_size: int
    page_title_size: int
    label_size: int
    help_text_size: int
    banner_text_size: int


@dataclass(frozen=True, slots=True)
class SettingsLayout:
    window_width: int = 640
    window_height: int = 620
    margin_left: int = 24
    header_title_top: int = 18
    header_title_width: int = 220
    header_title_height: int = 24
    header_subtitle_top: int = 44
    header_subtitle_width: int = 560
    header_subtitle_height: int = 20
    banner_top: int = 112
    banner_width: int = 580
    banner_height: int = 18
    navigation_top: int = 78
    navigation_button_width: int = 136
    navigation_button_height: int = 28
    navigation_button_gap: int = 8
    page_title_top: int = 124
    page_title_width: int = 220
    page_title_height: int = 22
    page_summary_top: int = 148
    page_summary_width: int = 580
    page_summary_height: int = 34
    content_top: int = 198
    page_hint_top: int = 520
    page_hint_width: int = 360
    page_hint_height: int = 18
    action_bar_top: int = 540
    default_button_width: int = 116
    preview_button_left: int = 152
    preview_button_width: int = 104
    cancel_button_left: int = 418
    action_button_width: int = 90
    button_height: int = 28
    label_x: int = 28
    label_width: int = 170
    field_x: int = 212
    field_width: int = 372
    row_gap: int = 54
    control_height: int = 24
    combo_height: int = 220
    help_text_offset_y: int = 26
    label_text_offset_y: int = 4
    hotkey_field_width: int = 248
    hotkey_button_gap: int = 12
    hotkey_button_width: int = 112
    panel_inset: int = 12
    panel_radius: int = 12
    banner_radius: int = 8
    radius_button_pill: int = 10

    def nav_button_left(self, index: int) -> int:
        return self.margin_left + index * (self.navigation_button_width + self.navigation_button_gap)

    @property
    def save_button_left(self) -> int:
        return self.cancel_button_left + self.action_button_width + 10

    @property
    def hotkey_button_left(self) -> int:
        return self.field_x + self.hotkey_field_width + self.hotkey_button_gap


@dataclass(frozen=True, slots=True)
class SettingsTheme:
    palette: SettingsPalette
    typography: SettingsTypography
    layout: SettingsLayout


DEFAULT_SETTINGS_THEME = SettingsTheme(
    palette=SettingsPalette(
        app_background="#F3F7FB",
        panel_background="#F8FBFF",
        card_background="#B8FFFFFF",
        surface_elevated="#FCFEFF",
        surface_card="#FBFDFF",
        border_subtle="#D8E2EE",
        border_focus="#77AEEB",
        divider_subtle="#E5EDF5",
        text_primary="#17324D",
        text_secondary="#5F748A",
        text_muted="#7E91A5",
        accent_primary="#4D8FEA",
        accent_pressed="#3C7DDD",
        accent_soft="#EAF3FF",
        status_success="#2F7D45",
        status_success_fill="#EDF9F1",
        status_success_border="#8FCE9B",
        status_error="#D85C5C",
        status_error_fill="#FFF1F1",
        status_error_border="#E7A6A6",
        status_info="#5B8FD6",
        status_info_fill="#EDF5FF",
    ),
    typography=SettingsTypography(
        window_title_size=18,
        page_title_size=15,
        label_size=13,
        help_text_size=12,
        banner_text_size=12,
    ),
    layout=SettingsLayout(),
)
