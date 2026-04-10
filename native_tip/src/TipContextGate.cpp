#include "TipContextGate.h"

namespace native_tip {

bool TipContextGate::canBindContext(bool serviceActive, bool endpointBound, const std::wstring& contextId) const {
    return serviceActive && endpointBound && !contextId.empty();
}

bool TipContextGate::canStartSession(bool serviceActive, bool endpointBound, const std::wstring& contextId) const {
    return canBindContext(serviceActive, endpointBound, contextId);
}

}  // namespace native_tip
