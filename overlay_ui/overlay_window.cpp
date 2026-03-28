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
    text_ = std::move(payload.text);
    if (text_.empty()) {
        Hide();
        return;
    }

    if (text_changed || text_layout_ == nullptr) {
        RebuildLayout();
    }

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
    if (!text_.empty()) {
        RebuildLayout();
        Render();
    }
}

void OverlayWindow::Stop() {
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
        D2D1::ColorF(0.0F, 0.0F, 0.0F, 0.20F),
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

    UpdateGeometry();
}

void OverlayWindow::UpdateGeometry() {
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

    const float scale = DpiScale();
    const float padding_x = kPaddingXDip * scale;
    const float padding_y = kPaddingYDip * scale;
    const int min_width = static_cast<int>(std::ceil(kMinWidthDip * scale));

    width_px_ = std::max(
        min_width,
        static_cast<int>(std::ceil(metrics.widthIncludingTrailingWhitespace + padding_x * 2.0F))
    );
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
    if (hwnd_ == nullptr || text_layout_ == nullptr || width_px_ <= 0 || height_px_ <= 0) {
        return;
    }
    if (!EnsureDeviceResources()) {
        return;
    }

    HDC screen_dc = GetDC(nullptr);
    HDC memory_dc = CreateCompatibleDC(screen_dc);
    BITMAPINFO bitmap_info{};
    bitmap_info.bmiHeader.biSize = sizeof(BITMAPINFOHEADER);
    bitmap_info.bmiHeader.biWidth = width_px_;
    bitmap_info.bmiHeader.biHeight = -height_px_;
    bitmap_info.bmiHeader.biPlanes = 1;
    bitmap_info.bmiHeader.biBitCount = 32;
    bitmap_info.bmiHeader.biCompression = BI_RGB;

    HBITMAP bitmap = CreateDIBSection(memory_dc, &bitmap_info, DIB_RGB_COLORS, nullptr, nullptr, 0);
    if (bitmap == nullptr) {
        DeleteDC(memory_dc);
        ReleaseDC(nullptr, screen_dc);
        return;
    }

    const HGDIOBJ old_bitmap = SelectObject(memory_dc, bitmap);
    const RECT paint_rect{0, 0, width_px_, height_px_};
    HRESULT hr = dc_render_target_->BindDC(memory_dc, &paint_rect);
    if (SUCCEEDED(hr)) {
        dc_render_target_->BeginDraw();
        dc_render_target_->SetTransform(D2D1::Matrix3x2F::Identity());
        dc_render_target_->Clear(D2D1::ColorF(0.0F, 0.0F, 0.0F, 0.0F));

        background_brush_->SetOpacity(style_.opacity * current_opacity_);
        border_brush_->SetOpacity(0.22F * current_opacity_);
        shadow_brush_->SetOpacity(0.18F * current_opacity_);
        text_brush_->SetOpacity(current_opacity_);

        const float scale = DpiScale();
        const float corner_radius = kCornerRadiusDip * scale;
        const float border_width = kBorderWidthDip * scale;
        const float shadow_offset_y = kShadowOffsetYDip * scale;
        const float shadow_inset = kShadowInsetDip * scale;
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
        dc_render_target_->DrawRoundedRectangle(rounded_rect, border_brush_.Get(), border_width);
        dc_render_target_->DrawTextLayout(
            D2D1::Point2F(kPaddingXDip * scale, kPaddingYDip * scale),
            text_layout_.Get(),
            text_brush_.Get()
        );

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
        UpdateLayeredWindow(hwnd_, screen_dc, &destination, &size, memory_dc, &source, 0, &blend, ULW_ALPHA);
    } else {
        Log("render failed hr=" + HrToString(hr));
    }

    SelectObject(memory_dc, old_bitmap);
    DeleteObject(bitmap);
    DeleteDC(memory_dc);
    ReleaseDC(nullptr, screen_dc);
}

void OverlayWindow::StartAnimation(float target_opacity) {
    target_opacity_ = std::clamp(target_opacity, 0.0F, 1.0F);
    animation_start_opacity_ = current_opacity_;
    animation_started_at_ = std::chrono::steady_clock::now();
    if (style_.animation_ms <= 0) {
        current_opacity_ = target_opacity_;
        if (current_opacity_ <= 0.0F && window_visible_) {
            ShowWindow(hwnd_, SW_HIDE);
            window_visible_ = false;
            visibility_state_ = VisibilityState::Hidden;
        } else {
            visibility_state_ = VisibilityState::Visible;
            Render();
        }
        return;
    }
    SetTimer(hwnd_, kAnimationTimerId, 16, nullptr);
}

void OverlayWindow::TickAnimation() {
    const auto now = std::chrono::steady_clock::now();
    const auto elapsed_ms = std::chrono::duration_cast<std::chrono::milliseconds>(now - animation_started_at_).count();
    const int animation_duration = std::max(style_.animation_ms, 1);
    const float t = std::clamp(static_cast<float>(elapsed_ms) / static_cast<float>(animation_duration), 0.0F, 1.0F);
    const float eased = 1.0F - std::pow(1.0F - t, 3.0F);
    current_opacity_ = animation_start_opacity_ + (target_opacity_ - animation_start_opacity_) * eased;

    if (current_opacity_ > 0.0F && !window_visible_) {
        ShowWindow(hwnd_, SW_SHOWNOACTIVATE);
        window_visible_ = true;
    }

    Render();

    if (t >= 1.0F) {
        current_opacity_ = target_opacity_;
        KillTimer(hwnd_, kAnimationTimerId);
        if (current_opacity_ <= 0.0F && window_visible_) {
            ShowWindow(hwnd_, SW_HIDE);
            window_visible_ = false;
            visibility_state_ = VisibilityState::Hidden;
        } else {
            visibility_state_ = VisibilityState::Visible;
        }
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

}  // namespace overlay_ui
