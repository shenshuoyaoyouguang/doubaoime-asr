#include "overlay_window.h"

#include <algorithm>
#include <cmath>
#include <memory>
#include <sstream>
#include <string>

#include <dwmapi.h>

namespace overlay_ui {

namespace {

constexpr wchar_t kWindowClassName[] = L"DoubaoOverlayWindow";
constexpr float kMaxTextHeightDip = 1200.0F;
constexpr float kMinWidthDip = 120.0F;
constexpr float kPaddingXDip = 18.0F;
constexpr float kPaddingYDip = 12.0F;
constexpr float kCornerRadiusDip = 10.0F;
constexpr float kBorderWidthDip = 1.0F;
constexpr float kShadowOffsetYDip = 4.0F;
constexpr float kShadowInsetDip = 3.0F;
constexpr int kMinMarginDip = 20;

#ifndef DWMWA_WINDOW_CORNER_PREFERENCE
#define DWMWA_WINDOW_CORNER_PREFERENCE 33
#endif

#ifndef DWMWA_SYSTEMBACKDROP_TYPE
#define DWMWA_SYSTEMBACKDROP_TYPE 38
#endif

#ifndef DWMWCP_ROUND
enum DWM_WINDOW_CORNER_PREFERENCE {
    DWMWCP_DEFAULT = 0,
    DWMWCP_DONOTROUND = 1,
    DWMWCP_ROUND = 2,
    DWMWCP_ROUNDSMALL = 3,
};
#endif

#ifndef DWMSBT_TRANSIENTWINDOW
enum DWM_SYSTEMBACKDROP_TYPE {
    DWMSBT_AUTO = 0,
    DWMSBT_NONE = 1,
    DWMSBT_MAINWINDOW = 2,
    DWMSBT_TRANSIENTWINDOW = 3,
    DWMSBT_TABBEDWINDOW = 4,
};
#endif

std::string HrToString(HRESULT hr) {
    std::ostringstream stream;
    stream << "0x" << std::hex << static_cast<unsigned long>(hr);
    return stream.str();
}

}  // namespace

OverlayWindow::OverlayWindow(Logger logger)
    : logger_(std::move(logger)) {}

bool OverlayWindow::Create(HINSTANCE instance_handle) {
    instance_handle_ = instance_handle;

    WNDCLASSEXW window_class{};
    window_class.cbSize = sizeof(window_class);
    window_class.lpfnWndProc = &OverlayWindow::WindowProc;
    window_class.hInstance = instance_handle_;
    window_class.lpszClassName = kWindowClassName;
    window_class.hCursor = LoadCursorW(nullptr, IDC_ARROW);

    if (RegisterClassExW(&window_class) == 0) {
        const DWORD error = GetLastError();
        if (error != ERROR_CLASS_ALREADY_EXISTS) {
            Log("register window class failed error=" + std::to_string(error));
            return false;
        }
    }

    hwnd_ = CreateWindowExW(
        WS_EX_LAYERED | WS_EX_TOPMOST | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE | WS_EX_TRANSPARENT,
        kWindowClassName,
        L"",
        WS_POPUP,
        CW_USEDEFAULT,
        CW_USEDEFAULT,
        0,
        0,
        nullptr,
        nullptr,
        instance_handle_,
        this
    );

    if (hwnd_ == nullptr) {
        Log("create window failed error=" + std::to_string(GetLastError()));
        return false;
    }

    if (!InitializeFactories()) {
        return false;
    }

    ApplyWindowAttributes();
    ShowWindow(hwnd_, SW_HIDE);
    UpdateWindow(hwnd_);
    return true;
}

int OverlayWindow::Run() {
    MSG message{};
    while (GetMessageW(&message, nullptr, 0, 0) > 0) {
        TranslateMessage(&message);
        DispatchMessageW(&message);
    }
    return static_cast<int>(message.wParam);
}

void OverlayWindow::ShowText(OverlayShowPayload payload) {
    if (payload.seq < last_seq_) {
        return;
    }
    last_seq_ = payload.seq;
    const bool text_changed = text_ != payload.text;
    const bool kind_changed = kind_ != payload.kind;
    text_ = std::move(payload.text);
    kind_ = std::move(payload.kind);
    stable_prefix_utf16_len_ = std::min<unsigned long long>(payload.stable_prefix_utf16_len, text_.size());

    // 麦克风模式：空文字但显示界面
    const bool is_microphone = kind_ == L"microphone";

    if (text_.empty() && !is_microphone) {
        Hide();
        return;
    }

    if (text_changed || kind_changed || text_layout_ == nullptr) {
        if (!text_.empty()) {
            RebuildLayout();
        }
    } else if (prefix_layout_ == nullptr && stable_prefix_utf16_len_ > 0 && stable_prefix_utf16_len_ < text_.size()) {
        RebuildPrefixLayout();
    }

    // 初始化麦克风动画时间
    if (is_microphone) {
        microphone_started_at_ = std::chrono::steady_clock::now();
        UpdateGeometry();  // 麦克风模式需要更新尺寸
    }

    // 初始化高亮动画时间
    if (kind_changed || text_changed) {
        highlight_started_at_ = std::chrono::steady_clock::now();
    }

    tail_animation_started_at_ = std::chrono::steady_clock::now();

    if (!window_visible_) {
        ShowWindow(hwnd_, SW_SHOWNOACTIVATE);
        window_visible_ = true;
    }

    if (visibility_state_ == VisibilityState::Hidden || visibility_state_ == VisibilityState::Hiding || current_opacity_ <= 0.0F) {
        visibility_state_ = VisibilityState::Showing;
        StartAnimation(1.0F);
        return;
    }

    target_opacity_ = 1.0F;
    visibility_state_ = VisibilityState::Visible;
    if (ShouldAnimateTail() || is_microphone) {
        SetTimer(hwnd_, kAnimationTimerId, 16, nullptr);
    }
    Render();
}

void OverlayWindow::Hide() {
    if (visibility_state_ == VisibilityState::Hidden || visibility_state_ == VisibilityState::Hiding) {
        return;
    }
    visibility_state_ = VisibilityState::Hiding;
    StartAnimation(0.0F);
}

void OverlayWindow::Configure(OverlayStyle style) {
    style.font_size = std::clamp(style.font_size, 10.0F, 36.0F);
    style.max_width = std::clamp(style.max_width, 320.0F, 1200.0F);
    style.opacity = std::clamp(style.opacity, 0.35F, 1.0F);
    style.bottom_offset = std::clamp(style.bottom_offset, 20, 500);
    style.animation_ms = std::clamp(style.animation_ms, 0, 600);

    const bool changed = std::fabs(style.font_size - style_.font_size) > 0.01F
        || std::fabs(style.max_width - style_.max_width) > 0.01F
        || std::fabs(style.opacity - style_.opacity) > 0.001F
        || style.bottom_offset != style_.bottom_offset
        || style.animation_ms != style_.animation_ms;
    style_ = style;
    if (!changed) {
        return;
    }

    text_format_.Reset();
    prefix_layout_.Reset();
    session_peak_width_px_ = 0;
    if (!text_.empty()) {
        RebuildLayout();
        Render();
    }
}

void OverlayWindow::Stop() {
    ReleaseBitmapResources();
    if (hwnd_ != nullptr) {
        DestroyWindow(hwnd_);
        hwnd_ = nullptr;
    }
}

LRESULT CALLBACK OverlayWindow::WindowProc(HWND hwnd, UINT message, WPARAM wparam, LPARAM lparam) {
    if (message == WM_NCCREATE) {
        const auto* create_struct = reinterpret_cast<CREATESTRUCTW*>(lparam);
        auto* window = static_cast<OverlayWindow*>(create_struct->lpCreateParams);
        SetWindowLongPtrW(hwnd, GWLP_USERDATA, reinterpret_cast<LONG_PTR>(window));
        window->hwnd_ = hwnd;
    }

    auto* window = reinterpret_cast<OverlayWindow*>(GetWindowLongPtrW(hwnd, GWLP_USERDATA));
    if (window == nullptr) {
        return DefWindowProcW(hwnd, message, wparam, lparam);
    }
    return window->HandleMessage(message, wparam, lparam);
}

LRESULT OverlayWindow::HandleMessage(UINT message, WPARAM wparam, LPARAM lparam) {
    switch (message) {
    case WM_APP_OVERLAY_SHOW: {
        std::unique_ptr<OverlayShowPayload> payload(reinterpret_cast<OverlayShowPayload*>(lparam));
        if (payload) {
            ShowText(std::move(*payload));
        } else {
            ShowText(OverlayShowPayload{});
        }
        return 0;
    }
    case WM_APP_OVERLAY_HIDE:
        Hide();
        return 0;
    case WM_APP_OVERLAY_CONFIGURE: {
        std::unique_ptr<OverlayStyle> style(reinterpret_cast<OverlayStyle*>(lparam));
        if (style) {
            Configure(*style);
        }
        return 0;
    }
    case WM_APP_OVERLAY_STOP:
        Stop();
        return 0;
    case WM_TIMER:
        if (wparam == kAnimationTimerId) {
            TickAnimation();
            return 0;
        }
        break;
    case WM_DISPLAYCHANGE:
    case WM_DPICHANGED:
    case WM_SETTINGCHANGE:
        if (!text_.empty() && (window_visible_ || current_opacity_ > 0.0F)) {
            RebuildLayout();
            Render();
        }
        return 0;
    case WM_ERASEBKGND:
        return 1;
    case WM_NCHITTEST:
        return HTTRANSPARENT;
    case WM_DESTROY:
        KillTimer(hwnd_, kAnimationTimerId);
        ReleaseBitmapResources();
        PostQuitMessage(0);
        return 0;
    default:
        break;
    }
    return DefWindowProcW(hwnd_, message, wparam, lparam);
}

bool OverlayWindow::InitializeFactories() {
    HRESULT hr = D2D1CreateFactory(D2D1_FACTORY_TYPE_SINGLE_THREADED, d2d_factory_.ReleaseAndGetAddressOf());
    if (FAILED(hr)) {
        Log("create d2d factory failed hr=" + HrToString(hr));
        return false;
    }

    hr = DWriteCreateFactory(
        DWRITE_FACTORY_TYPE_SHARED,
        __uuidof(IDWriteFactory),
        reinterpret_cast<IUnknown**>(dwrite_factory_.ReleaseAndGetAddressOf())
    );
    if (FAILED(hr)) {
        Log("create dwrite factory failed hr=" + HrToString(hr));
        return false;
    }

    if (!EnsureDeviceResources()) {
        return false;
    }
    return EnsureTextFormat();
}

bool OverlayWindow::EnsureTextFormat() {
    const float scale = DpiScale();
    const float font_size_dip = style_.font_size * scale;
    if (text_format_ != nullptr && std::fabs(font_size_dip_ - font_size_dip) < 0.01F) {
        return true;
    }

    font_size_dip_ = font_size_dip;
    text_format_.Reset();

    HRESULT hr = dwrite_factory_->CreateTextFormat(
        L"Microsoft YaHei UI",
        nullptr,
        DWRITE_FONT_WEIGHT_NORMAL,
        DWRITE_FONT_STYLE_NORMAL,
        DWRITE_FONT_STRETCH_NORMAL,
        font_size_dip_,
        L"zh-CN",
        text_format_.ReleaseAndGetAddressOf()
    );
    if (FAILED(hr)) {
        Log("create text format failed hr=" + HrToString(hr));
        return false;
    }

    text_format_->SetWordWrapping(DWRITE_WORD_WRAPPING_WRAP);
    text_format_->SetTextAlignment(DWRITE_TEXT_ALIGNMENT_LEADING);
    text_format_->SetParagraphAlignment(DWRITE_PARAGRAPH_ALIGNMENT_NEAR);
    return true;
}

bool OverlayWindow::EnsureDeviceResources() {
    if (dc_render_target_ != nullptr) {
        return true;
    }

    const D2D1_RENDER_TARGET_PROPERTIES properties = D2D1::RenderTargetProperties(
        D2D1_RENDER_TARGET_TYPE_DEFAULT,
        D2D1::PixelFormat(DXGI_FORMAT_B8G8R8A8_UNORM, D2D1_ALPHA_MODE_PREMULTIPLIED),
        96.0F,
        96.0F
    );

    HRESULT hr = d2d_factory_->CreateDCRenderTarget(&properties, dc_render_target_.ReleaseAndGetAddressOf());
    if (FAILED(hr)) {
        Log("create dc render target failed hr=" + HrToString(hr));
        return false;
    }

    hr = dc_render_target_->CreateSolidColorBrush(
        D2D1::ColorF(0.07F, 0.07F, 0.07F, 0.84F),
        background_brush_.ReleaseAndGetAddressOf()
    );
    if (FAILED(hr)) {
        Log("create background brush failed hr=" + HrToString(hr));
        return false;
    }

    hr = dc_render_target_->CreateSolidColorBrush(
        D2D1::ColorF(1.0F, 1.0F, 1.0F, 0.18F),
        border_brush_.ReleaseAndGetAddressOf()
    );
    if (FAILED(hr)) {
        Log("create border brush failed hr=" + HrToString(hr));
        return false;
    }

    hr = dc_render_target_->CreateSolidColorBrush(
        D2D1::ColorF(0.0F, 0.0F, 0.0F, 0.08F),
        shadow_brush_.ReleaseAndGetAddressOf()
    );
    if (FAILED(hr)) {
        Log("create shadow brush failed hr=" + HrToString(hr));
        return false;
    }

    hr = dc_render_target_->CreateSolidColorBrush(
        D2D1::ColorF(1.0F, 1.0F, 1.0F, 1.0F),
        text_brush_.ReleaseAndGetAddressOf()
    );
    if (FAILED(hr)) {
        Log("create text brush failed hr=" + HrToString(hr));
        return false;
    }

    dc_render_target_->SetTextAntialiasMode(D2D1_TEXT_ANTIALIAS_MODE_GRAYSCALE);
    return true;
}

bool OverlayWindow::EnsureBitmapResources() {
    if (screen_dc_ == nullptr) {
        screen_dc_ = GetDC(nullptr);
        if (screen_dc_ == nullptr) {
            Log("acquire screen dc failed");
            return false;
        }
    }

    if (memory_dc_ == nullptr) {
        memory_dc_ = CreateCompatibleDC(screen_dc_);
        if (memory_dc_ == nullptr) {
            Log("create memory dc failed error=" + std::to_string(GetLastError()));
            ReleaseDC(nullptr, screen_dc_);
            screen_dc_ = nullptr;
            return false;
        }
    }

    if (bitmap_ != nullptr && bitmap_width_px_ == width_px_ && bitmap_height_px_ == height_px_) {
        return true;
    }

    if (bitmap_ != nullptr) {
        if (memory_dc_default_bitmap_ != nullptr) {
            SelectObject(memory_dc_, memory_dc_default_bitmap_);
        }
        DeleteObject(bitmap_);
        bitmap_ = nullptr;
    }

    BITMAPINFO bitmap_info{};
    bitmap_info.bmiHeader.biSize = sizeof(BITMAPINFOHEADER);
    bitmap_info.bmiHeader.biWidth = width_px_;
    bitmap_info.bmiHeader.biHeight = -height_px_;
    bitmap_info.bmiHeader.biPlanes = 1;
    bitmap_info.bmiHeader.biBitCount = 32;
    bitmap_info.bmiHeader.biCompression = BI_RGB;

    HBITMAP bitmap = CreateDIBSection(memory_dc_, &bitmap_info, DIB_RGB_COLORS, nullptr, nullptr, 0);
    if (bitmap == nullptr) {
        Log("create dib section failed error=" + std::to_string(GetLastError()));
        bitmap_width_px_ = 0;
        bitmap_height_px_ = 0;
        return false;
    }

    const HGDIOBJ previous_bitmap = SelectObject(memory_dc_, bitmap);
    if (previous_bitmap == nullptr || previous_bitmap == HGDI_ERROR) {
        Log("select bitmap failed error=" + std::to_string(GetLastError()));
        DeleteObject(bitmap);
        bitmap_width_px_ = 0;
        bitmap_height_px_ = 0;
        return false;
    }
    if (memory_dc_default_bitmap_ == nullptr) {
        memory_dc_default_bitmap_ = previous_bitmap;
    }

    bitmap_ = bitmap;
    bitmap_width_px_ = width_px_;
    bitmap_height_px_ = height_px_;
    return true;
}

void OverlayWindow::ApplyWindowAttributes() {
    if (hwnd_ == nullptr) {
        return;
    }

    const DWM_WINDOW_CORNER_PREFERENCE corner_preference = DWMWCP_ROUND;
    const HRESULT corner_hr = DwmSetWindowAttribute(
        hwnd_,
        DWMWA_WINDOW_CORNER_PREFERENCE,
        &corner_preference,
        sizeof(corner_preference)
    );
    Log("apply corner preference hr=" + HrToString(corner_hr));

    const DWM_SYSTEMBACKDROP_TYPE backdrop_type = DWMSBT_TRANSIENTWINDOW;
    const HRESULT backdrop_hr = DwmSetWindowAttribute(
        hwnd_,
        DWMWA_SYSTEMBACKDROP_TYPE,
        &backdrop_type,
        sizeof(backdrop_type)
    );
    Log("apply backdrop hr=" + HrToString(backdrop_hr));
}

void OverlayWindow::RebuildLayout() {
    if (!EnsureTextFormat()) {
        return;
    }

    const float scale = DpiScale();
    const float max_width = style_.max_width * scale;
    const float max_height = kMaxTextHeightDip * scale;

    text_layout_.Reset();
    HRESULT hr = dwrite_factory_->CreateTextLayout(
        text_.c_str(),
        static_cast<UINT32>(text_.size()),
        text_format_.Get(),
        max_width,
        max_height,
        text_layout_.ReleaseAndGetAddressOf()
    );
    if (FAILED(hr)) {
        Log("create text layout failed hr=" + HrToString(hr));
        return;
    }

    RebuildPrefixLayout();
    UpdateGeometry();
}

void OverlayWindow::RebuildPrefixLayout() {
    prefix_layout_.Reset();
    if (stable_prefix_utf16_len_ == 0 || stable_prefix_utf16_len_ >= text_.size()) {
        return;
    }
    if (!EnsureTextFormat()) {
        return;
    }

    const float scale = DpiScale();
    const float max_width = style_.max_width * scale;
    const float max_height = kMaxTextHeightDip * scale;
    const std::wstring prefix_text = text_.substr(0, static_cast<std::size_t>(stable_prefix_utf16_len_));

    HRESULT hr = dwrite_factory_->CreateTextLayout(
        prefix_text.c_str(),
        static_cast<UINT32>(prefix_text.size()),
        text_format_.Get(),
        max_width,
        max_height,
        prefix_layout_.ReleaseAndGetAddressOf()
    );
    if (FAILED(hr)) {
        Log("create prefix layout failed hr=" + HrToString(hr));
    }
}

void OverlayWindow::UpdateGeometry() {
    const float scale = DpiScale();

    // 麦克风模式：使用固定正方形尺寸
    if (IsMicrophoneMode()) {
        const int box_size = static_cast<int>(std::ceil(kMicrophoneBoxSizeDip * scale));
        width_px_ = box_size;
        height_px_ = box_size;
        session_peak_width_px_ = 0;  // 麦克风模式不记录 peak width

        MONITORINFO monitor_info{};
        monitor_info.cbSize = sizeof(monitor_info);
        const HMONITOR monitor = MonitorFromWindow(hwnd_, MONITOR_DEFAULTTOPRIMARY);
        if (!GetMonitorInfoW(monitor, &monitor_info)) {
            return;
        }

        const RECT work_area = monitor_info.rcWork;
        const int margin = static_cast<int>(std::round(kMinMarginDip * scale));
        const int bottom_offset = static_cast<int>(std::round(static_cast<float>(style_.bottom_offset) * scale));
        const int work_width = work_area.right - work_area.left;
        const int work_height = work_area.bottom - work_area.top;

        x_px_ = std::max(work_area.left + margin, work_area.left + (work_width - width_px_) / 2);
        y_px_ = std::max(work_area.top + margin, work_area.top + work_height - height_px_ - bottom_offset);
        return;
    }

    // 文字模式：根据文字内容计算尺寸
    if (text_layout_ == nullptr) {
        width_px_ = 0;
        height_px_ = 0;
        return;
    }

    DWRITE_TEXT_METRICS metrics{};
    if (FAILED(text_layout_->GetMetrics(&metrics))) {
        width_px_ = 0;
        height_px_ = 0;
        return;
    }

    const float padding_x = kPaddingXDip * scale;
    const float padding_y = kPaddingYDip * scale;
    const int min_width = static_cast<int>(std::ceil(kMinWidthDip * scale));

    const int natural_width_px = std::max(
        min_width,
        static_cast<int>(std::ceil(metrics.widthIncludingTrailingWhitespace + padding_x * 2.0F))
    );
    const int max_width_px = static_cast<int>(std::ceil(style_.max_width * scale + padding_x * 2.0F));
    const int clamped_width_px = std::min(max_width_px, natural_width_px);
    session_peak_width_px_ = std::max(session_peak_width_px_, clamped_width_px);
    width_px_ = session_peak_width_px_;
    height_px_ = static_cast<int>(std::ceil(metrics.height + padding_y * 2.0F));

    MONITORINFO monitor_info{};
    monitor_info.cbSize = sizeof(monitor_info);
    const HMONITOR monitor = MonitorFromWindow(hwnd_, MONITOR_DEFAULTTOPRIMARY);
    if (!GetMonitorInfoW(monitor, &monitor_info)) {
        return;
    }

    const RECT work_area = monitor_info.rcWork;
    const int margin = static_cast<int>(std::round(kMinMarginDip * scale));
    const int bottom_offset = static_cast<int>(std::round(static_cast<float>(style_.bottom_offset) * scale));
    const int work_width = work_area.right - work_area.left;
    const int work_height = work_area.bottom - work_area.top;

    x_px_ = std::max(work_area.left + margin, work_area.left + (work_width - width_px_) / 2);
    y_px_ = std::max(work_area.top + margin, work_area.top + work_height - height_px_ - bottom_offset);
}

void OverlayWindow::Render() {
    if (hwnd_ == nullptr || width_px_ <= 0 || height_px_ <= 0) {
        return;
    }

    const bool is_microphone = IsMicrophoneMode();
    if (!is_microphone && text_layout_ == nullptr) {
        return;
    }
    if (!EnsureDeviceResources() || !EnsureBitmapResources()) {
        return;
    }

    const RECT paint_rect{0, 0, width_px_, height_px_};
    HRESULT hr = dc_render_target_->BindDC(memory_dc_, &paint_rect);
    if (SUCCEEDED(hr)) {
        dc_render_target_->BeginDraw();
        dc_render_target_->SetTransform(D2D1::Matrix3x2F::Identity());
        dc_render_target_->Clear(D2D1::ColorF(0.0F, 0.0F, 0.0F, 0.0F));

        const bool is_interim = kind_ == L"interim";
        const bool is_polishing = kind_ == L"polishing";
        const bool is_final_committed = kind_ == L"final_committed";
        const bool is_final_raw = kind_ == L"final_raw";
        // 现代简洁风格配色
        D2D1_COLOR_F background_color = D2D1::ColorF(0.98F, 0.98F, 0.98F);  // 浅白背景
        D2D1_COLOR_F border_color = D2D1::ColorF(0.85F, 0.85F, 0.85F);      // 柔和边框
        D2D1_COLOR_F text_color = D2D1::ColorF(0.15F, 0.15F, 0.15F);        // 深灰文字
        float background_opacity = style_.opacity * current_opacity_;
        float border_opacity = 0.60F * current_opacity_;
        float shadow_opacity = 0.12F * current_opacity_;
        float accent_opacity = 0.0F;

        if (is_interim) {
            border_color = D2D1::ColorF(0.30F, 0.55F, 0.85F);                // 蓝色边框（识别中）
            border_opacity = 0.80F * current_opacity_;
            accent_opacity = 0.35F * current_opacity_;
        } else if (is_polishing) {
            background_color = D2D1::ColorF(0.96F, 0.94F, 0.90F);           // 淡黄背景
            border_color = D2D1::ColorF(0.85F, 0.65F, 0.30F);               // 金边框
            text_color = D2D1::ColorF(0.25F, 0.20F, 0.15F);                 // 深棕文字
            background_opacity = std::min(1.0F, style_.opacity * 0.96F) * current_opacity_;
            border_opacity = 0.85F * current_opacity_;
            shadow_opacity = 0.15F * current_opacity_;
            accent_opacity = 0.50F * current_opacity_;
        } else if (is_final_committed) {
            border_color = D2D1::ColorF(0.25F, 0.50F, 0.75F);               // 蓝边框（最终）
            border_opacity = 0.90F * current_opacity_;
            accent_opacity = 0.55F * current_opacity_;
        } else if (is_final_raw) {
            border_color = D2D1::ColorF(0.75F, 0.75F, 0.75F);               // 灰边框
            border_opacity = 0.65F * current_opacity_;
            accent_opacity = 0.25F * current_opacity_;
        } else if (is_microphone) {
            background_color = D2D1::ColorF(0.95F, 0.95F, 0.97F);           // 麦克风模式背景
            border_color = D2D1::ColorF(0.35F, 0.60F, 0.90F);               // 蓝色边框
            border_opacity = 0.70F * current_opacity_;
            shadow_opacity = 0.18F * current_opacity_;
        }

        background_brush_->SetColor(background_color);
        background_brush_->SetOpacity(background_opacity);
        border_brush_->SetColor(border_color);
        border_brush_->SetOpacity(border_opacity);
        shadow_brush_->SetOpacity(shadow_opacity);
        text_brush_->SetColor(text_color);
        text_brush_->SetOpacity(current_opacity_);

        const float scale = DpiScale();
        const float corner_radius = kCornerRadiusDip * scale;
        const float border_width = kBorderWidthDip * scale;
        const float shadow_offset_y = 6.0F * scale;       // 更柔和的阴影偏移
        const float shadow_inset = 4.0F * scale;        // 阴影内缩
        const float accent_height = std::max(2.0F, std::round(2.0F * scale));
        const auto rounded_rect = D2D1::RoundedRect(
            D2D1::RectF(0.5F, 0.5F, static_cast<float>(width_px_) - 0.5F, static_cast<float>(height_px_) - 0.5F),
            corner_radius,
            corner_radius
        );
        const auto shadow_rect = D2D1::RoundedRect(
            D2D1::RectF(
                shadow_inset,
                shadow_inset + shadow_offset_y,
                static_cast<float>(width_px_) - shadow_inset,
                static_cast<float>(height_px_) - shadow_inset + shadow_offset_y
            ),
            corner_radius,
            corner_radius
        );

        dc_render_target_->FillRoundedRectangle(shadow_rect, shadow_brush_.Get());
        dc_render_target_->FillRoundedRectangle(rounded_rect, background_brush_.Get());
        if (accent_opacity > 0.0F) {
            border_brush_->SetOpacity(accent_opacity);
            dc_render_target_->FillRoundedRectangle(
                D2D1::RoundedRect(
                    D2D1::RectF(
                        10.0F * scale,
                        static_cast<float>(height_px_) - accent_height - 7.0F * scale,
                        static_cast<float>(width_px_) - 10.0F * scale,
                        static_cast<float>(height_px_) - 5.0F * scale
                    ),
                    accent_height,
                    accent_height
                ),
                border_brush_.Get()
            );
            border_brush_->SetOpacity(border_opacity);
        }
        dc_render_target_->DrawRoundedRectangle(rounded_rect, border_brush_.Get(), border_width);

        // 麦克风模式渲染
        if (is_microphone) {
            RenderMicrophoneMode();
        } else {
            // 文字渲染
            const D2D1_POINT_2F text_origin = D2D1::Point2F(kPaddingXDip * scale, kPaddingYDip * scale);
            if (ShouldAnimateTail() && prefix_layout_ != nullptr) {
                const auto now = std::chrono::steady_clock::now();
                const auto elapsed_ms = std::chrono::duration_cast<std::chrono::milliseconds>(now - tail_animation_started_at_).count();
                const float pulse = 0.5F + 0.5F * std::sin(static_cast<float>(elapsed_ms) / 120.0F);
                const float tail_opacity = current_opacity_ * (0.58F + 0.20F * pulse);
                text_brush_->SetOpacity(tail_opacity);
                dc_render_target_->DrawTextLayout(text_origin, text_layout_.Get(), text_brush_.Get());
                text_brush_->SetOpacity(current_opacity_);
                dc_render_target_->DrawTextLayout(text_origin, prefix_layout_.Get(), text_brush_.Get());
            } else {
                text_brush_->SetOpacity(current_opacity_);
                dc_render_target_->DrawTextLayout(text_origin, text_layout_.Get(), text_brush_.Get());
            }
        }

        hr = dc_render_target_->EndDraw();
    }

    if (SUCCEEDED(hr)) {
        POINT destination{x_px_, y_px_};
        SIZE size{width_px_, height_px_};
        POINT source{0, 0};
        BLENDFUNCTION blend{};
        blend.BlendOp = AC_SRC_OVER;
        blend.SourceConstantAlpha = 255;
        blend.AlphaFormat = AC_SRC_ALPHA;
        UpdateLayeredWindow(hwnd_, screen_dc_, &destination, &size, memory_dc_, &source, 0, &blend, ULW_ALPHA);
    } else {
        Log("render failed hr=" + HrToString(hr));
    }
}

bool OverlayWindow::ShouldAnimateTail() const {
    return kind_ == L"interim"
        && stable_prefix_utf16_len_ < text_.size()
        && prefix_layout_ != nullptr
        && current_opacity_ > 0.0F;
}

void OverlayWindow::ReleaseBitmapResources() {
    if (memory_dc_ != nullptr && bitmap_ != nullptr && memory_dc_default_bitmap_ != nullptr) {
        SelectObject(memory_dc_, memory_dc_default_bitmap_);
    }
    if (bitmap_ != nullptr) {
        DeleteObject(bitmap_);
        bitmap_ = nullptr;
    }
    if (memory_dc_ != nullptr) {
        DeleteDC(memory_dc_);
        memory_dc_ = nullptr;
    }
    if (screen_dc_ != nullptr) {
        ReleaseDC(nullptr, screen_dc_);
        screen_dc_ = nullptr;
    }
    memory_dc_default_bitmap_ = nullptr;
    bitmap_width_px_ = 0;
    bitmap_height_px_ = 0;
}

void OverlayWindow::StartAnimation(float target_opacity) {
    target_opacity_ = std::clamp(target_opacity, 0.0F, 1.0F);
    animation_start_opacity_ = current_opacity_;
    animation_started_at_ = std::chrono::steady_clock::now();
    if (style_.animation_ms <= 0) {
        current_opacity_ = target_opacity_;
        if (current_opacity_ <= 0.0F && window_visible_) {
            KillTimer(hwnd_, kAnimationTimerId);
            ShowWindow(hwnd_, SW_HIDE);
            window_visible_ = false;
            visibility_state_ = VisibilityState::Hidden;
            session_peak_width_px_ = 0;
        } else {
            visibility_state_ = VisibilityState::Visible;
            Render();
            if (ShouldAnimateTail() || IsMicrophoneMode()) {
                SetTimer(hwnd_, kAnimationTimerId, 16, nullptr);
            } else {
                KillTimer(hwnd_, kAnimationTimerId);
            }
        }
        return;
    }
    SetTimer(hwnd_, kAnimationTimerId, 16, nullptr);
}

void OverlayWindow::TickAnimation() {
    const bool is_microphone = IsMicrophoneMode();
    float t = 1.0F;

    if (visibility_state_ == VisibilityState::Showing || visibility_state_ == VisibilityState::Hiding) {
        const auto now = std::chrono::steady_clock::now();
        const auto elapsed_ms = std::chrono::duration_cast<std::chrono::milliseconds>(now - animation_started_at_).count();
        const int animation_duration = std::max(style_.animation_ms, 1);
        t = std::clamp(static_cast<float>(elapsed_ms) / static_cast<float>(animation_duration), 0.0F, 1.0F);

        // Back Ease-Out 缓动函数（弹性效果）
        const float c1 = 1.70158F;
        const float c3 = c1 + 1.0F;
        const float eased = 1.0F + c3 * std::pow(t - 1.0F, 3.0F) + c1 * std::pow(t - 1.0F, 2.0F);
        current_opacity_ = animation_start_opacity_ + (target_opacity_ - animation_start_opacity_) * std::clamp(eased, 0.0F, 1.0F);
    } else {
        current_opacity_ = target_opacity_;
    }

    if (current_opacity_ > 0.0F && !window_visible_) {
        ShowWindow(hwnd_, SW_SHOWNOACTIVATE);
        window_visible_ = true;
    }

    Render();

    if ((visibility_state_ == VisibilityState::Showing || visibility_state_ == VisibilityState::Hiding) && t >= 1.0F) {
        current_opacity_ = target_opacity_;
        if (current_opacity_ <= 0.0F && window_visible_) {
            ShowWindow(hwnd_, SW_HIDE);
            window_visible_ = false;
            visibility_state_ = VisibilityState::Hidden;
            session_peak_width_px_ = 0;
        } else {
            visibility_state_ = VisibilityState::Visible;
        }
    }

    const bool keep_timer =
        visibility_state_ == VisibilityState::Showing
        || visibility_state_ == VisibilityState::Hiding
        || ShouldAnimateTail()
        || (is_microphone && visibility_state_ != VisibilityState::Hidden && window_visible_);
    if (!keep_timer) {
        KillTimer(hwnd_, kAnimationTimerId);
    }
}

void OverlayWindow::Log(const std::string& message) const {
    if (logger_) {
        logger_(message);
    }
}

float OverlayWindow::DpiScale() const {
    if (hwnd_ == nullptr) {
        return 1.0F;
    }
    const UINT dpi = GetDpiForWindow(hwnd_);
    if (dpi == 0) {
        return 1.0F;
    }
    return static_cast<float>(dpi) / 96.0F;
}

bool OverlayWindow::IsMicrophoneMode() const {
    return kind_ == L"microphone";
}

void OverlayWindow::RenderMicrophoneMode() {
    const float scale = DpiScale();
    const float center_x = static_cast<float>(width_px_) / 2.0F;
    const float center_y = static_cast<float>(height_px_) / 2.0F;

    // 计算麦克风模式已运行的时间
    const auto now = std::chrono::steady_clock::now();
    const auto elapsed_ms = std::chrono::duration_cast<std::chrono::milliseconds>(
        now - microphone_started_at_
    ).count();

    // 绘制波纹动画
    DrawRipples(center_x, center_y, static_cast<float>(elapsed_ms));

    // 计算麦克风震动偏移
    const float shake_offset_x = kMicrophoneShakeAmplitude * scale *
        std::sin(static_cast<float>(elapsed_ms) / 1000.0F * kMicrophoneShakeFrequency * 2.0F * 3.14159F);
    const float shake_offset_y = kMicrophoneShakeAmplitude * scale * 0.5F *
        std::cos(static_cast<float>(elapsed_ms) / 1000.0F * kMicrophoneShakeFrequency * 2.5F * 3.14159F);

    // 绘制麦克风图标（带震动偏移）
    const float icon_size = kMicrophoneIconSizeDip * scale;
    DrawMicrophoneIcon(center_x + shake_offset_x, center_y + shake_offset_y, icon_size, current_opacity_);
}

void OverlayWindow::DrawMicrophoneIcon(float center_x, float center_y, float size, float opacity) {
    // 麦克风图标绘制（使用 Direct2D 绘制简洁的麦克风形状）
    const float half_width = size * 0.35F;       // 麦克风头宽度的一半
    const float head_height = size * 0.55F;      // 麦克风头高度
    const float stem_width = size * 0.12F;       // 麦克风杆宽度
    const float stem_height = size * 0.25F;      // 麦克风杆高度
    const float arc_radius = size * 0.15F;       // 底部弧线半径

    // 创建麦克风图标专用画笔（蓝色渐变风格）
    Microsoft::WRL::ComPtr<ID2D1SolidColorBrush> mic_brush;
    dc_render_target_->CreateSolidColorBrush(
        D2D1::ColorF(0.30F, 0.55F, 0.85F, opacity),
        mic_brush.ReleaseAndGetAddressOf()
    );

    // 麦克风头（圆角矩形）
    const float head_top = center_y - head_height * 0.6F;
    const D2D1_RECT_F head_rect = D2D1::RectF(
        center_x - half_width,
        head_top,
        center_x + half_width,
        head_top + head_height
    );
    const D2D1_ROUNDED_RECT head_rounded = D2D1::RoundedRect(head_rect, half_width, half_width);
    dc_render_target_->FillRoundedRectangle(head_rounded, mic_brush.Get());

    // 麦克风杆（矩形）
    const float stem_top = head_top + head_height - 2.0F;
    dc_render_target_->FillRectangle(
        D2D1::RectF(
            center_x - stem_width / 2.0F,
            stem_top,
            center_x + stem_width / 2.0F,
            stem_top + stem_height
        ),
        mic_brush.Get()
    );

    // 底部弧线（使用椭圆的上半部分模拟）
    const float arc_center_y = stem_top + stem_height + arc_radius * 0.3F;
    const D2D1_ELLIPSE arc_ellipse = D2D1::Ellipse(
        D2D1::Point2F(center_x, arc_center_y),
        half_width * 1.3F,
        arc_radius
    );
    dc_render_target_->DrawEllipse(arc_ellipse, mic_brush.Get(), stem_width);

    // 麦克风底部小横线（表示支架）
    dc_render_target_->FillRectangle(
        D2D1::RectF(
            center_x - half_width * 1.3F,
            arc_center_y + arc_radius * 0.6F,
            center_x + half_width * 1.3F,
            arc_center_y + arc_radius * 0.6F + stem_width * 0.8F
        ),
        mic_brush.Get()
    );
}

void OverlayWindow::DrawRipples(float center_x, float center_y, float elapsed_ms) {
    const float scale = DpiScale();
    const float max_radius = kRippleMaxRadiusDip * scale;

    // 创建波纹画笔（半透明蓝色）
    Microsoft::WRL::ComPtr<ID2D1SolidColorBrush> ripple_brush;
    dc_render_target_->CreateSolidColorBrush(
        D2D1::ColorF(0.30F, 0.55F, 0.85F, 0.0F),
        ripple_brush.ReleaseAndGetAddressOf()
    );

    // 绘制多个波纹（错开相位）
    for (int i = 0; i < kRippleCount; ++i) {
        // 计算当前波纹的相位（错开启动时间）
        const float phase_offset = static_cast<float>(i) / static_cast<float>(kRippleCount);
        const float phase = std::fmod(elapsed_ms / kRippleDurationMs + phase_offset, 1.0F);

        // 波纹从中心向外扩散，透明度随距离衰减
        const float radius = phase * max_radius;
        const float opacity = current_opacity_ * (1.0F - phase) * 0.35F;

        if (opacity > 0.01F && radius > 1.0F) {
            ripple_brush->SetOpacity(opacity);
            const D2D1_ELLIPSE ripple_ellipse = D2D1::Ellipse(
                D2D1::Point2F(center_x, center_y),
                radius,
                radius
            );
            dc_render_target_->DrawEllipse(ripple_ellipse, ripple_brush.Get(), 2.0F * scale);
        }
    }
}

}  // namespace overlay_ui
