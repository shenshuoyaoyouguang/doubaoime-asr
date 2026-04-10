#pragma once

#include <string>

namespace native_tip {

class TipEditSession {
public:
    bool registerActiveContext(const std::wstring& contextId, bool editSessionReady);
    bool clearActiveContext();
    bool canBeginForContext(const std::wstring& contextId) const;
    bool beginForContext(const std::wstring& contextId);
    void complete();
    bool hasActiveContext() const;
    bool editSessionReady() const;
    bool inProgress() const;
    const std::wstring& activeContextId() const;
    const std::wstring& editingContextId() const;

private:
    std::wstring activeContextId_;
    std::wstring editingContextId_;
    bool editSessionReady_ = false;
    bool inProgress_ = false;
};

}  // namespace native_tip
