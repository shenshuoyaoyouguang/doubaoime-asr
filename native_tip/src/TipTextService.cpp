#include "TipTextService.h"

#include <Windows.h>

#include <cctype>
#include <optional>
#include <string>

namespace native_tip {

namespace {

std::wstring utf8ToWide(const std::string& text) {
    if (text.empty()) {
        return L"";
    }
    const int length = MultiByteToWideChar(CP_UTF8, 0, text.data(), static_cast<int>(text.size()), nullptr, 0);
    if (length <= 0) {
        return L"";
    }
    std::wstring result(static_cast<std::size_t>(length), L'\0');
    MultiByteToWideChar(CP_UTF8, 0, text.data(), static_cast<int>(text.size()), result.data(), length);
    return result;
}

std::optional<std::string> findJsonStringValue(const std::string& line, const std::string& key) {
    const std::string needle = "\"" + key + "\"";
    const std::size_t keyPos = line.find(needle);
    if (keyPos == std::string::npos) {
        return std::nullopt;
    }
    std::size_t valuePos = line.find(':', keyPos + needle.size());
    if (valuePos == std::string::npos) {
        return std::nullopt;
    }
    ++valuePos;
    while (valuePos < line.size() && std::isspace(static_cast<unsigned char>(line[valuePos])) != 0) {
        ++valuePos;
    }
    if (valuePos >= line.size() || line[valuePos] != '"') {
        return std::nullopt;
    }
    ++valuePos;
    const std::size_t endPos = line.find('"', valuePos);
    if (endPos == std::string::npos) {
        return std::nullopt;
    }
    return line.substr(valuePos, endPos - valuePos);
}

std::optional<bool> findJsonBoolValue(const std::string& line, const std::string& key) {
    const std::string needle = "\"" + key + "\"";
    const std::size_t keyPos = line.find(needle);
    if (keyPos == std::string::npos) {
        return std::nullopt;
    }
    std::size_t valuePos = line.find(':', keyPos + needle.size());
    if (valuePos == std::string::npos) {
        return std::nullopt;
    }
    ++valuePos;
    while (valuePos < line.size() && std::isspace(static_cast<unsigned char>(line[valuePos])) != 0) {
        ++valuePos;
    }
    if (line.compare(valuePos, 4, "true") == 0) {
        return true;
    }
    if (line.compare(valuePos, 5, "false") == 0) {
        return false;
    }
    return std::nullopt;
}

std::string jsonEscape(const std::string& text) {
    std::string escaped;
    escaped.reserve(text.size());
    for (const char ch : text) {
        switch (ch) {
        case '\\':
            escaped += "\\\\";
            break;
        case '"':
            escaped += "\\\"";
            break;
        case '\n':
            escaped += "\\n";
            break;
        case '\r':
            escaped += "\\r";
            break;
        case '\t':
            escaped += "\\t";
            break;
        default:
            escaped += ch;
            break;
        }
    }
    return escaped;
}

std::string makeGatewayResponse(
    const std::string& name,
    const std::string& sessionId,
    bool ok,
    const std::string& reason,
    const std::string& extraPayload = "",
    const std::optional<bool> cleanupPerformed = std::nullopt) {
    std::string payload = "\"ok\": ";
    payload += ok ? "true" : "false";
    if (!reason.empty()) {
        payload += ", \"reason\": \"" + jsonEscape(reason) + "\"";
    }
    if (!extraPayload.empty()) {
        payload += ", " + extraPayload;
    }
    if (cleanupPerformed.has_value()) {
        payload += ", \"cleanup_performed\": ";
        payload += cleanupPerformed.value() ? "true" : "false";
    }
    return "{\"version\": 1, \"kind\": \"event\", \"name\": \"" + name + "\", \"session_id\": \"" +
        jsonEscape(sessionId) + "\", \"payload\": {" + payload + "}}";
}

}  // namespace

void TipTextService::trace(const std::wstring& message) {
    traceSink_.append(message);
}

bool TipTextService::activate() {
    active_ = true;
    sessionPhase_ = TipSessionPhase::Active;
    trace(L"activate");
    return true;
}

bool TipTextService::deactivate() {
    active_ = false;
    fallbackRequired_ = false;
    fallbackReason_ = FallbackReason::None;
    activeContextId_.clear();
    rendezvousContextId_.clear();
    editSession_.clearActiveContext();
    serviceClient_.disconnect();
    writerOwner_ = WriterOwner::None;
    if (composition_.isActive()) {
        composition_.cancelComposition();
    }
    sessionPhase_ = TipSessionPhase::Idle;
    trace(L"deactivate");
    return true;
}

bool TipTextService::bindServiceEndpoint(const std::wstring& endpoint) {
    const bool ok = serviceClient_.bindEndpoint(endpoint);
    if (ok) {
        sessionPhase_ = TipSessionPhase::EndpointBound;
        trace(L"bindServiceEndpoint");
    }
    return ok;
}

bool TipTextService::bindServiceSessionId(const std::wstring& sessionId) {
    serviceSessionId_ = sessionId;
    if (serviceSessionId_.empty()) {
        return false;
    }
    trace(L"bindServiceSessionId");
    return true;
}

bool TipTextService::connectService() {
    const bool ok = serviceClient_.connectBoundEndpoint();
    if (ok) {
        sessionPhase_ = TipSessionPhase::ServiceConnected;
        trace(L"connectService");
    }
    return ok;
}

bool TipTextService::disconnectService() {
    const bool ok = serviceClient_.disconnect();
    if (ok) {
        sessionPhase_ = serviceClient_.hasEndpoint() ? TipSessionPhase::EndpointBound : TipSessionPhase::Active;
    }
    trace(L"disconnectService");
    return ok;
}

bool TipTextService::registerActiveContext(const std::wstring& contextId) {
    if (!editSession_.registerActiveContext(contextId, true)) {
        return false;
    }
    if (!active_) {
        activate();
    }
    rendezvousContextId_ = editSession_.activeContextId();
    trace(L"registerActiveContext");
    return true;
}

bool TipTextService::clearActiveContext() {
    rendezvousContextId_.clear();
    editSession_.clearActiveContext();
    trace(L"clearActiveContext");
    return true;
}

bool TipTextService::bindActiveContext(const std::wstring& contextId) {
    if (!contextGate_.canBindContext(active_, serviceClient_.hasEndpoint(), contextId)) {
        return false;
    }
    activeContextId_ = contextId;
    sessionPhase_ = TipSessionPhase::ContextBound;
    trace(L"bindActiveContext");
    return !activeContextId_.empty();
}

bool TipTextService::startFeasibilitySession() {
    if (!serviceClient_.isConnected()) {
        return false;
    }
    if (!contextGate_.canStartSession(active_, serviceClient_.hasEndpoint(), activeContextId_)) {
        return false;
    }
    fallbackRequired_ = false;
    fallbackReason_ = FallbackReason::None;
    writerOwner_ = WriterOwner::None;
    sessionPhase_ = TipSessionPhase::Composing;
    trace(L"startFeasibilitySession");
    return composition_.startComposition();
}

bool TipTextService::beginGatewaySession(const std::wstring& sessionId, const std::wstring& contextId) {
    if (sessionId.empty() || contextId.empty() || contextId.rfind(L"hwnd:", 0) != 0) {
        return false;
    }
    if (!editSession_.canBeginForContext(contextId)) {
        trace(L"beginGatewaySession_rendezvous_mismatch");
        return false;
    }
    if (!active_) {
        activate();
    }
    if (composition_.isActive()) {
        composition_.cancelComposition();
    }
    serviceSessionId_ = sessionId;
    activeContextId_ = contextId;
    fallbackRequired_ = false;
    fallbackReason_ = FallbackReason::None;
    writerOwner_ = WriterOwner::None;
    if (!editSession_.beginForContext(contextId)) {
        trace(L"beginGatewaySession_edit_session_unavailable");
        return false;
    }
    sessionPhase_ = TipSessionPhase::Composing;
    trace(L"beginGatewaySession");
    return composition_.startComposition();
}

bool TipTextService::updateInterimText(const std::wstring& text) {
    trace(L"updateInterimText");
    return composition_.updateComposition(text);
}

bool TipTextService::commitResolvedFinal(const std::wstring& text) {
    if (!composition_.commitResolvedFinal(text)) {
        return false;
    }
    editSession_.complete();
    writerOwner_ = WriterOwner::Tip;
    sessionPhase_ = TipSessionPhase::Committed;
    trace(L"commitResolvedFinal");
    return true;
}

bool TipTextService::applyGatewayInterimText(const std::wstring& sessionId, const std::wstring& text) {
    if (sessionId != serviceSessionId_) {
        trace(L"applyGatewayInterimText_session_mismatch");
        return false;
    }
    trace(L"applyGatewayInterimText");
    return updateInterimText(text);
}

bool TipTextService::applyGatewayResolvedFinalText(const std::wstring& sessionId, const std::wstring& text) {
    if (sessionId != serviceSessionId_) {
        trace(L"applyGatewayResolvedFinalText_session_mismatch");
        return false;
    }
    trace(L"applyGatewayResolvedFinalText");
    return commitResolvedFinal(text);
}

bool TipTextService::applyServiceInterimText(const std::wstring& text) {
    if (sessionPhase_ != TipSessionPhase::Composing) {
        return false;
    }
    trace(L"applyServiceInterimText");
    return updateInterimText(text);
}

bool TipTextService::applyServiceResolvedFinalText(const std::wstring& text) {
    if (sessionPhase_ != TipSessionPhase::Composing) {
        return false;
    }
    trace(L"applyServiceResolvedFinalText");
    return commitResolvedFinal(text);
}

bool TipTextService::pumpServiceEvent() {
    std::string line;
    if (!serviceClient_.readLine(&line)) {
        return false;
    }
    const auto kind = findJsonStringValue(line, "kind");
    if (!kind.has_value() || kind.value() != "event") {
        return false;
    }
    const auto name = findJsonStringValue(line, "name");
    if (!name.has_value()) {
        return false;
    }
    if (name.value() == "interim") {
        const auto sessionId = findJsonStringValue(line, "session_id");
        const auto text = findJsonStringValue(line, "text");
        if (!sessionId.has_value() || utf8ToWide(sessionId.value()) != serviceSessionId_) {
            trace(L"pumpServiceEvent_session_mismatch");
            return false;
        }
        trace(L"pumpServiceEvent_interim");
        return text.has_value() && applyServiceInterimText(utf8ToWide(text.value()));
    }
    if (name.value() == "final_resolved") {
        const auto sessionId = findJsonStringValue(line, "session_id");
        const auto text = findJsonStringValue(line, "text");
        if (!sessionId.has_value() || utf8ToWide(sessionId.value()) != serviceSessionId_) {
            trace(L"pumpServiceEvent_session_mismatch");
            return false;
        }
        trace(L"pumpServiceEvent_final_resolved");
        return text.has_value() && applyServiceResolvedFinalText(utf8ToWide(text.value()));
    }
    if (name.value() == "final_raw") {
        trace(L"pumpServiceEvent_final_raw_ignored");
        return true;
    }
    trace(L"pumpServiceEvent_ignored");
    return true;
}

bool TipTextService::cancelSession() {
    if (!composition_.cancelComposition()) {
        return false;
    }
    writerOwner_ = WriterOwner::None;
    fallbackReason_ = FallbackReason::None;
    sessionPhase_ = TipSessionPhase::ServiceConnected;
    trace(L"cancelSession");
    return true;
}

bool TipTextService::cancelGatewaySession(const std::wstring& sessionId) {
    if (sessionId != serviceSessionId_) {
        trace(L"cancelGatewaySession_session_mismatch");
        return false;
    }
    if (writerOwner_ == WriterOwner::Tip && !composition_.isActive()) {
        trace(L"cancelGatewaySession_already_committed");
        return false;
    }
    if (!composition_.isActive()) {
        trace(L"cancelGatewaySession_no_active_composition");
        return true;
    }
    const bool cancelled = composition_.cancelComposition();
    editSession_.complete();
    writerOwner_ = WriterOwner::None;
    fallbackReason_ = FallbackReason::None;
    sessionPhase_ = TipSessionPhase::Active;
    trace(L"cancelGatewaySession");
    return cancelled;
}

std::string TipTextService::processGatewayCommand(const std::string& line) {
    const auto kind = findJsonStringValue(line, "kind");
    const auto name = findJsonStringValue(line, "name");
    const auto sessionId = findJsonStringValue(line, "session_id");
    if (!kind.has_value() || kind.value() != "command" || !name.has_value() || !sessionId.has_value()) {
        return makeGatewayResponse("error", sessionId.value_or(""), false, "tip_protocol_error");
    }
    if (name.value() == "begin_session") {
        const auto contextId = findJsonStringValue(line, "context_id");
        if (!contextId.has_value()) {
            return makeGatewayResponse("error", sessionId.value(), false, "tip_context_missing");
        }
        const bool ok = beginGatewaySession(utf8ToWide(sessionId.value()), utf8ToWide(contextId.value()));
        return makeGatewayResponse(ok ? "ack" : "error", sessionId.value(), ok, ok ? "" : "tip_context_not_active");
    }
    if (name.value() == "register_active_context") {
        const auto contextId = findJsonStringValue(line, "context_id");
        if (!contextId.has_value()) {
            return makeGatewayResponse("error", sessionId.value(), false, "tip_context_missing");
        }
        const bool editSessionReady = findJsonBoolValue(line, "edit_session_ready").value_or(true);
        const bool ok = editSession_.registerActiveContext(utf8ToWide(contextId.value()), editSessionReady);
        if (ok) {
            if (!active_) {
                activate();
            }
            rendezvousContextId_ = editSession_.activeContextId();
            trace(L"registerActiveContext");
        }
        return makeGatewayResponse(ok ? "ack" : "error", sessionId.value(), ok, ok ? "" : "tip_context_invalid");
    }
    if (name.value() == "clear_active_context") {
        const bool ok = clearActiveContext();
        return makeGatewayResponse(ok ? "ack" : "error", sessionId.value(), ok, ok ? "" : "tip_context_clear_failed");
    }
    if (name.value() == "query_active_context") {
        std::string extraPayload;
        if (!rendezvousContextId_.empty()) {
            const int size =
                WideCharToMultiByte(CP_UTF8, 0, rendezvousContextId_.c_str(), -1, nullptr, 0, nullptr, nullptr);
            if (size > 1) {
                std::string utf8Context(static_cast<std::size_t>(size - 1), '\0');
                WideCharToMultiByte(
                    CP_UTF8,
                    0,
                    rendezvousContextId_.c_str(),
                    -1,
                    utf8Context.data(),
                    size - 1,
                    nullptr,
                    nullptr);
                extraPayload = "\"active_context_id\": \"" + jsonEscape(utf8Context) + "\", \"edit_session_ready\": ";
                extraPayload += editSession_.editSessionReady() ? "true" : "false";
            }
        }
        return makeGatewayResponse("ack", sessionId.value(), true, "", extraPayload);
    }
    if (name.value() == "interim") {
        const auto text = findJsonStringValue(line, "text");
        const bool ok = text.has_value() && applyGatewayInterimText(utf8ToWide(sessionId.value()), utf8ToWide(text.value()));
        return makeGatewayResponse(ok ? "ack" : "error", sessionId.value(), ok, ok ? "" : "tip_interim_failed");
    }
    if (name.value() == "commit_resolved_final") {
        const auto text = findJsonStringValue(line, "text");
        const bool ok =
            text.has_value() && applyGatewayResolvedFinalText(utf8ToWide(sessionId.value()), utf8ToWide(text.value()));
        return makeGatewayResponse(ok ? "ack" : "error", sessionId.value(), ok, ok ? "" : "tip_commit_failed");
    }
    if (name.value() == "cancel_session") {
        const bool ok = cancelGatewaySession(utf8ToWide(sessionId.value()));
        return makeGatewayResponse(
            ok ? "ack" : "error",
            sessionId.value(),
            ok,
            ok ? "" : "composition_cleanup_failed",
            "",
            ok ? std::optional<bool>(true) : std::optional<bool>(false));
    }
    return makeGatewayResponse("error", sessionId.value(), false, "tip_command_unsupported");
}

bool TipTextService::invalidateContext() {
    if (writerOwner_ == WriterOwner::Tip && !composition_.isActive()) {
        activeContextId_.clear();
        fallbackReason_ = FallbackReason::None;
        sessionPhase_ = TipSessionPhase::Committed;
        trace(L"invalidateContext_preserve_tip_owner");
        return true;
    }
    if (!composition_.isActive()) {
        activeContextId_.clear();
        fallbackRequired_ = true;
        fallbackReason_ = FallbackReason::ContextInvalidated;
        writerOwner_ = WriterOwner::Legacy;
        sessionPhase_ = TipSessionPhase::FallbackPending;
        trace(L"invalidateContext_require_fallback");
        return true;
    }
    const bool cancelled = composition_.cancelComposition();
    activeContextId_.clear();
    fallbackRequired_ = true;
    fallbackReason_ = FallbackReason::CompositionCleanupRequired;
    writerOwner_ = WriterOwner::Legacy;
    sessionPhase_ = TipSessionPhase::FallbackPending;
    trace(L"invalidateContext_cancel_and_fallback");
    return cancelled;
}

bool TipTextService::requiresFallback() const {
    return fallbackRequired_;
}

WriterOwner TipTextService::writerOwner() const {
    return writerOwner_;
}

FallbackReason TipTextService::fallbackReason() const {
    return fallbackReason_;
}

TipSessionPhase TipTextService::sessionPhase() const {
    return sessionPhase_;
}

const std::wstring& TipTextService::activeContextId() const {
    return activeContextId_;
}

bool TipTextService::serviceEndpointBound() const {
    return serviceClient_.hasEndpoint();
}

bool TipTextService::serviceConnected() const {
    return serviceClient_.isConnected();
}

const std::wstring& TipTextService::serviceSessionId() const {
    return serviceSessionId_;
}

const std::wstring& TipTextService::rendezvousContextId() const {
    return rendezvousContextId_;
}

bool TipTextService::rendezvousEditSessionReady() const {
    return editSession_.editSessionReady();
}

std::size_t TipTextService::traceCount() const {
    return traceSink_.size();
}

std::wstring TipTextService::lastTrace() const {
    return traceSink_.last();
}

}  // namespace native_tip
