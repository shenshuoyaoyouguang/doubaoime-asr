#pragma once

#include <string>
#include <string>

#include "TipContextGate.h"
#include "TipCompositionManager.h"
#include "TipEditSession.h"
#include "TipServiceClient.h"
#include "TipTraceSink.h"

namespace native_tip {

enum class WriterOwner {
    None,
    Tip,
    Legacy,
};

enum class FallbackReason {
    None,
    ContextInvalidated,
    CompositionCleanupRequired,
};

enum class TipSessionPhase {
    Idle,
    Active,
    EndpointBound,
    ContextBound,
    ServiceConnected,
    Composing,
    Committed,
    FallbackPending,
};

class TipTextService {
public:
    bool activate();
    bool deactivate();
    bool bindServiceEndpoint(const std::wstring& endpoint);
    bool bindServiceSessionId(const std::wstring& sessionId);
    bool connectService();
    bool disconnectService();
    bool registerActiveContext(const std::wstring& contextId);
    bool clearActiveContext();
    bool bindActiveContext(const std::wstring& contextId);
    bool startFeasibilitySession();
    bool beginGatewaySession(const std::wstring& sessionId, const std::wstring& contextId);
    bool updateInterimText(const std::wstring& text);
    bool commitResolvedFinal(const std::wstring& text);
    bool applyGatewayInterimText(const std::wstring& sessionId, const std::wstring& text);
    bool applyGatewayResolvedFinalText(const std::wstring& sessionId, const std::wstring& text);
    bool applyServiceInterimText(const std::wstring& text);
    bool applyServiceResolvedFinalText(const std::wstring& text);
    bool pumpServiceEvent();
    bool cancelSession();
    bool cancelGatewaySession(const std::wstring& sessionId);
    std::string processGatewayCommand(const std::string& line);
    bool invalidateContext();
    bool requiresFallback() const;
    WriterOwner writerOwner() const;
    FallbackReason fallbackReason() const;
    TipSessionPhase sessionPhase() const;
    const std::wstring& activeContextId() const;
    bool serviceEndpointBound() const;
    bool serviceConnected() const;
    const std::wstring& serviceSessionId() const;
    const std::wstring& rendezvousContextId() const;
    bool rendezvousEditSessionReady() const;
    std::size_t traceCount() const;
    std::wstring lastTrace() const;

private:
    void trace(const std::wstring& message);

    bool active_ = false;
    bool fallbackRequired_ = false;
    std::wstring activeContextId_;
    std::wstring rendezvousContextId_;
    std::wstring serviceSessionId_;
    WriterOwner writerOwner_ = WriterOwner::None;
    FallbackReason fallbackReason_ = FallbackReason::None;
    TipSessionPhase sessionPhase_ = TipSessionPhase::Idle;
    TipContextGate contextGate_;
    TipCompositionManager composition_;
    TipEditSession editSession_;
    TipServiceClient serviceClient_;
    TipTraceSink traceSink_;
};

}  // namespace native_tip
