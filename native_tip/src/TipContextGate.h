#pragma once

#include <string>

namespace native_tip {

class TipContextGate {
public:
    bool canBindContext(bool serviceActive, bool endpointBound, const std::wstring& contextId) const;
    bool canStartSession(bool serviceActive, bool endpointBound, const std::wstring& contextId) const;
};

}  // namespace native_tip
