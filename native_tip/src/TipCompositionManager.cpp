#include "TipCompositionManager.h"

namespace native_tip {

bool TipCompositionManager::startComposition() {
    active_ = true;
    buffer_.clear();
    return true;
}

bool TipCompositionManager::updateComposition(const std::wstring& text) {
    if (!active_) {
        return false;
    }
    buffer_ = text;
    return true;
}

bool TipCompositionManager::commitResolvedFinal(const std::wstring& text) {
    if (!active_) {
        return false;
    }
    buffer_ = text;
    active_ = false;
    return true;
}

bool TipCompositionManager::cancelComposition() {
    if (!active_) {
        return false;
    }
    buffer_.clear();
    active_ = false;
    return true;
}

bool TipCompositionManager::isActive() const {
    return active_;
}

const std::wstring& TipCompositionManager::buffer() const {
    return buffer_;
}

}  // namespace native_tip
