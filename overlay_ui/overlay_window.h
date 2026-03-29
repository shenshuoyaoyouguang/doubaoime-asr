#pragma once

#ifndef NOMINMAX
#define NOMINMAX
#endif

#include <windows.h>

#include <d2d1.h>
#include <dwrite.h>
#include <wrl/client.h>

#include <chrono>
#include <functional>
#include <string>

namespace overlay_ui {

constexpr UINT WM_APP_OVERLAY_SHOW = WM_APP + 1;
constexpr UINT WM_APP_OVERLAY_HIDE = WM_APP + 2;
constexpr UINT WM_APP_OVERLAY_STOP = WM_APP + 3;
constexpr UINT WM_APP_OVERLAY_CONFIGURE = WM_APP + 4;

// 麦克风模式相关常量
constexpr float kMicrophoneBoxSizeDip = 160.0F;        // 麦克风框尺寸
constexpr float kMicrophoneIconSizeDip = 48.0F;        // 麦克风图标尺寸
constexpr float kRippleMaxRadiusDip = 80.0F;           // 波纹最大半径
constexpr float kRippleDurationMs = 2000.0F;           // 波纹周期（毫秒）
constexpr int kRippleCount = 3;                        // 波纹数量
constexpr float kMicrophoneShakeAmplitude = 2.0F;      // 麦克风震动幅度
constexpr float kMicrophoneShakeFrequency = 8.0F;      // 麦克风震动频率（Hz）

struct OverlayStyle {
    float font_size = 14.0F;
    float max_width = 620.0F;
    float opacity = 0.92F;
    int bottom_offset = 120;
    int animation_ms = 150;
    bool modern_style = true;                          // 现代简洁风格
    float highlight_duration_ms = 300.0F;              // 高亮闪烁持续时间
};

struct OverlayShowPayload {
    std::wstring text;
    unsigned long long seq = 0;
    std::wstring kind = L"interim";
    unsigned long long stable_prefix_utf16_len = 0;
};

class OverlayWindow {
public:
    using Logger = std::function<void(const std::string&)>;

    explicit OverlayWindow(Logger logger);
    OverlayWindow(const OverlayWindow&) = delete;
    OverlayWindow& operator=(const OverlayWindow&) = delete;
    OverlayWindow(OverlayWindow&&) = delete;
    OverlayWindow& operator=(OverlayWindow&&) = delete;

    bool Create(HINSTANCE instance_handle);
    int Run();

    HWND hwnd() const noexcept { return hwnd_; }
    void ShowText(OverlayShowPayload payload);
    void Hide();
    void Configure(OverlayStyle style);
    void Stop();

private:
    static constexpr UINT_PTR kAnimationTimerId = 1;
    enum class VisibilityState {
        Hidden,
        Showing,
        Visible,
        Hiding,
    };

    static LRESULT CALLBACK WindowProc(HWND hwnd, UINT message, WPARAM wparam, LPARAM lparam);
    LRESULT HandleMessage(UINT message, WPARAM wparam, LPARAM lparam);

    bool InitializeFactories();
    bool EnsureTextFormat();
    bool EnsureDeviceResources();
    bool EnsureBitmapResources();
    void ApplyWindowAttributes();
    void RebuildLayout();
    void RebuildPrefixLayout();
    void UpdateGeometry();
    void ReleaseBitmapResources();
    void Render();
    bool ShouldAnimateTail() const;
    bool IsMicrophoneMode() const;
    void StartAnimation(float target_opacity);
    void TickAnimation();
    void DrawMicrophoneIcon(float center_x, float center_y, float size, float opacity);
    void DrawRipples(float center_x, float center_y, float elapsed_ms);
    void RenderMicrophoneMode();
    void Log(const std::string& message) const;
    float DpiScale() const;

    Logger logger_;
    HWND hwnd_ = nullptr;
    HINSTANCE instance_handle_ = nullptr;
    bool window_visible_ = false;
    VisibilityState visibility_state_ = VisibilityState::Hidden;
    OverlayStyle style_{};

    std::wstring text_;
    std::wstring kind_ = L"interim";
    unsigned long long stable_prefix_utf16_len_ = 0;
    unsigned long long last_seq_ = 0;
    float current_opacity_ = 0.0F;
    float target_opacity_ = 0.0F;
    float animation_start_opacity_ = 0.0F;
    std::chrono::steady_clock::time_point animation_started_at_{};
    std::chrono::steady_clock::time_point tail_animation_started_at_{};
    std::chrono::steady_clock::time_point microphone_started_at_{};  // 麦克风模式开始时间
    std::chrono::steady_clock::time_point highlight_started_at_{};   // 高亮动画开始时间
    int width_px_ = 0;
    int height_px_ = 0;
    int x_px_ = 0;
    int y_px_ = 0;
    int session_peak_width_px_ = 0;
    float font_size_dip_ = 12.0F;

    Microsoft::WRL::ComPtr<ID2D1Factory> d2d_factory_;
    Microsoft::WRL::ComPtr<ID2D1DCRenderTarget> dc_render_target_;
    Microsoft::WRL::ComPtr<IDWriteFactory> dwrite_factory_;
    Microsoft::WRL::ComPtr<IDWriteTextFormat> text_format_;
    Microsoft::WRL::ComPtr<IDWriteTextLayout> text_layout_;
    Microsoft::WRL::ComPtr<IDWriteTextLayout> prefix_layout_;
    Microsoft::WRL::ComPtr<ID2D1SolidColorBrush> background_brush_;
    Microsoft::WRL::ComPtr<ID2D1SolidColorBrush> border_brush_;
    Microsoft::WRL::ComPtr<ID2D1SolidColorBrush> shadow_brush_;
    Microsoft::WRL::ComPtr<ID2D1SolidColorBrush> text_brush_;
    HDC screen_dc_ = nullptr;
    HDC memory_dc_ = nullptr;
    HBITMAP bitmap_ = nullptr;
    HGDIOBJ memory_dc_default_bitmap_ = nullptr;
    int bitmap_width_px_ = 0;
    int bitmap_height_px_ = 0;
};

}  // namespace overlay_ui
