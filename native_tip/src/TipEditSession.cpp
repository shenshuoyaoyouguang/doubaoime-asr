#include "TipEditSession.h"

namespace native_tip {

bool TipEditSession::registerActiveContext(const std::wstring& contextId, bool editSessionReady) {
    if (contextId.empty() || contextId.rfind(L"hwnd:", 0) != 0) {
        return false;
    }
    activeContextId_ = contextId;
    editSessionReady_ = editSessionReady;
    if (editingContextId_ != activeContextId_) {
        editingContextId_.clear();
        inProgress_ = false;
    }
    return true;
}

bool TipEditSession::clearActiveContext() {
    activeContextId_.clear();
    editingContextId_.clear();
    editSessionReady_ = false;
    inProgress_ = false;
    return true;
}

bool TipEditSession::canBeginForContext(const std::wstring& contextId) const {
    return editSessionReady_ && !activeContextId_.empty() && activeContextId_ == contextId;
}

bool TipEditSession::beginForContext(const std::wstring& contextId) {
    if (!canBeginForContext(contextId)) {
        return false;
    }
    editingContextId_ = contextId;
    inProgress_ = true;
    return true;
}

void TipEditSession::complete() {
    editingContextId_.clear();
    inProgress_ = false;
}

bool TipEditSession::hasActiveContext() const {
    return !activeContextId_.empty();
}

bool TipEditSession::editSessionReady() const {
    return editSessionReady_;
}

bool TipEditSession::inProgress() const {
    return inProgress_;
}

const std::wstring& TipEditSession::activeContextId() const {
    return activeContextId_;
}

const std::wstring& TipEditSession::editingContextId() const {
    return editingContextId_;
}

}  // namespace native_tip
