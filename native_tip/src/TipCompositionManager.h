#pragma once

#include <string>

namespace native_tip {

class TipCompositionManager {
public:
    bool startComposition();
    bool updateComposition(const std::wstring& text);
    bool commitResolvedFinal(const std::wstring& text);
    bool cancelComposition();
    bool isActive() const;
    const std::wstring& buffer() const;

private:
    bool active_ = false;
    std::wstring buffer_;
};

}  // namespace native_tip
