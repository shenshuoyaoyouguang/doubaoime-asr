#include <cstdlib>
#include <iostream>
#include <string>
#include <thread>

#include <Windows.h>

#include "TipGatewayServer.h"
#include "TipTextService.h"

using native_tip::FallbackReason;
using native_tip::TipGatewayServer;
using native_tip::TipSessionPhase;
using native_tip::TipTextService;
using native_tip::WriterOwner;

namespace {

constexpr wchar_t kPipeName[] = L"\\\\.\\pipe\\doubao-tip-service";
constexpr wchar_t kGatewayPipeName[] = L"\\\\.\\pipe\\doubao-tip-gateway";
constexpr wchar_t kSessionId[] = L"tip-session-1";

void writePipeLine(HANDLE pipe, const std::string& line) {
    const std::string payload = line + "\n";
    DWORD bytesWritten = 0;
    if (!WriteFile(pipe, payload.data(), static_cast<DWORD>(payload.size()), &bytesWritten, nullptr) ||
        bytesWritten != payload.size()) {
        std::cerr << "WriteFile failed\n";
        std::exit(EXIT_FAILURE);
    }
}

std::string readPipeLine(HANDLE pipe) {
    std::string buffer;
    char chunk[256];
    while (true) {
        DWORD bytesRead = 0;
        if (!ReadFile(pipe, chunk, sizeof(chunk), &bytesRead, nullptr) || bytesRead == 0) {
            std::cerr << "ReadFile failed\n";
            std::exit(EXIT_FAILURE);
        }
        buffer.append(chunk, chunk + bytesRead);
        const std::size_t newline = buffer.find('\n');
        if (newline != std::string::npos) {
            return buffer.substr(0, newline);
        }
    }
}

HANDLE openPipeClient(const wchar_t* pipeName) {
    HANDLE handle = INVALID_HANDLE_VALUE;
    for (int attempt = 0; attempt < 100; ++attempt) {
        handle = CreateFileW(
            pipeName,
            GENERIC_READ | GENERIC_WRITE,
            0,
            nullptr,
            OPEN_EXISTING,
            0,
            nullptr);
        if (handle != INVALID_HANDLE_VALUE) {
            return handle;
        }
        Sleep(10);
    }
    std::cerr << "CreateFileW failed\n";
    std::exit(EXIT_FAILURE);
}

std::string gatewayRoundTrip(const wchar_t* pipeName, const std::string& line) {
    HANDLE client = openPipeClient(pipeName);
    writePipeLine(client, line);
    const std::string response = readPipeLine(client);
    CloseHandle(client);
    return response;
}

void serviceServerThread() {
    const HANDLE pipe = CreateNamedPipeW(
        kPipeName,
        PIPE_ACCESS_DUPLEX,
        PIPE_TYPE_BYTE | PIPE_READMODE_BYTE | PIPE_WAIT,
        1,
        4096,
        4096,
        0,
        nullptr);
    if (pipe == INVALID_HANDLE_VALUE) {
        std::cerr << "CreateNamedPipeW failed\n";
        std::exit(EXIT_FAILURE);
    }
    if (!ConnectNamedPipe(pipe, nullptr) && GetLastError() != ERROR_PIPE_CONNECTED) {
        std::cerr << "ConnectNamedPipe failed\n";
        CloseHandle(pipe);
        std::exit(EXIT_FAILURE);
    }
    writePipeLine(pipe, R"({"version": 1, "kind": "event", "name": "interim", "session_id": "stale-session", "payload": {"text": "stale"}})");
    writePipeLine(pipe, R"({"version": 1, "kind": "event", "name": "interim", "session_id": "tip-session-1", "payload": {"text": "hello"}})");
    writePipeLine(pipe, R"({"version": 1, "kind": "event", "name": "final_resolved", "session_id": "tip-session-1", "payload": {"text": "hello world"}})");
    FlushFileBuffers(pipe);
    DisconnectNamedPipe(pipe);
    CloseHandle(pipe);
}

}  // namespace

int main() {
    std::thread server(serviceServerThread);
    auto fail = [&](const char* message) -> int {
        std::cerr << message << "\n";
        if (server.joinable()) {
            server.detach();
        }
        return EXIT_FAILURE;
    };
    TipTextService service;
    if (service.bindActiveContext(L"should-fail-before-activate")) {
        return fail("bindActiveContext should fail before activate");
    }
    if (!service.activate()) {
        return fail("activate failed");
    }
    if (service.sessionPhase() != TipSessionPhase::Active) {
        return fail("sessionPhase should be Active after activate");
    }
    if (service.bindActiveContext(L"should-fail-before-endpoint")) {
        return fail("bindActiveContext should fail before bindServiceEndpoint");
    }
    if (!service.bindServiceEndpoint(kPipeName)) {
        return fail("bindServiceEndpoint failed");
    }
    if (!service.bindServiceSessionId(kSessionId)) {
        return fail("bindServiceSessionId failed");
    }
    if (service.sessionPhase() != TipSessionPhase::EndpointBound) {
        return fail("sessionPhase should be EndpointBound after bindServiceEndpoint");
    }
    if (!service.serviceEndpointBound()) {
        return fail("serviceEndpoint should be bound");
    }
    if (!service.bindActiveContext(L"notepad-context")) {
        return fail("bindActiveContext failed");
    }
    if (service.sessionPhase() != TipSessionPhase::ContextBound) {
        return fail("sessionPhase should be ContextBound after bindActiveContext");
    }
    if (service.startFeasibilitySession()) {
        return fail("startFeasibilitySession should fail before connectService");
    }
    if (!service.connectService()) {
        return fail("connectService failed");
    }
    if (!service.serviceConnected()) {
        return fail("service should report connected");
    }
    if (service.sessionPhase() != TipSessionPhase::ServiceConnected) {
        return fail("sessionPhase should be ServiceConnected after connectService");
    }
    if (!service.startFeasibilitySession()) {
        return fail("startFeasibilitySession failed");
    }
    if (service.sessionPhase() != TipSessionPhase::Composing) {
        return fail("sessionPhase should be Composing after startFeasibilitySession");
    }
    if (service.pumpServiceEvent()) {
        return fail("pumpServiceEvent should reject stale session event");
    }
    if (service.sessionPhase() != TipSessionPhase::Composing) {
        return fail("stale session event should not change composing phase");
    }
    if (!service.pumpServiceEvent()) {
        return fail("pumpServiceEvent interim failed");
    }
    if (!service.pumpServiceEvent()) {
        return fail("pumpServiceEvent final_resolved failed");
    }
    if (service.writerOwner() != WriterOwner::Tip) {
        return fail("writerOwner should stay Tip after commit");
    }
    if (service.sessionPhase() != TipSessionPhase::Committed) {
        return fail("sessionPhase should be Committed after commitResolvedFinal");
    }
    if (!service.invalidateContext()) {
        return fail("invalidateContext failed");
    }
    if (service.writerOwner() != WriterOwner::Tip) {
        return fail("invalidateContext incorrectly transferred ownership after commit");
    }
    if (service.requiresFallback()) {
        return fail("invalidateContext incorrectly forced fallback after commit");
    }
    if (service.fallbackReason() != FallbackReason::None) {
        return fail("fallbackReason should remain None after post-commit invalidation");
    }
    if (service.traceCount() == 0) {
        return fail("trace sink did not record lifecycle");
    }
    if (!service.disconnectService()) {
        return fail("disconnectService failed");
    }
    if (service.serviceConnected()) {
        return fail("service should be disconnected");
    }
    if (service.startFeasibilitySession()) {
        return fail("startFeasibilitySession should fail after disconnectService");
    }

    TipTextService gatewayService;
    const std::string registerResponse = gatewayService.processGatewayCommand(
        R"({"version": 1, "kind": "command", "name": "register_active_context", "session_id": "rendezvous", "payload": {"context_id": "hwnd:101"}})");
    if (registerResponse.find(R"("name": "ack")") == std::string::npos) {
        return fail("gateway register_active_context did not ack");
    }
    const std::string queryResponse = gatewayService.processGatewayCommand(
        R"({"version": 1, "kind": "command", "name": "query_active_context", "session_id": "rendezvous", "payload": {}})");
    if (queryResponse.find(R"("active_context_id": "hwnd:101")") == std::string::npos ||
        queryResponse.find(R"("edit_session_ready": true)") == std::string::npos) {
        return fail("gateway query_active_context did not expose registered context");
    }
    const std::string beginResponse = gatewayService.processGatewayCommand(
        R"({"version": 1, "kind": "command", "name": "begin_session", "session_id": "gateway-session", "payload": {"context_id": "hwnd:101"}})");
    if (beginResponse.find(R"("name": "ack")") == std::string::npos) {
        return fail("gateway begin_session did not ack");
    }
    const std::string interimResponse = gatewayService.processGatewayCommand(
        R"({"version": 1, "kind": "command", "name": "interim", "session_id": "gateway-session", "payload": {"text": "gateway interim"}})");
    if (interimResponse.find(R"("name": "ack")") == std::string::npos) {
        return fail("gateway interim did not ack");
    }
    const std::string staleResponse = gatewayService.processGatewayCommand(
        R"({"version": 1, "kind": "command", "name": "interim", "session_id": "stale-session", "payload": {"text": "stale"}})");
    if (staleResponse.find(R"("name": "error")") == std::string::npos) {
        return fail("gateway stale interim should error");
    }
    const std::string mismatchBeginResponse = gatewayService.processGatewayCommand(
        R"({"version": 1, "kind": "command", "name": "begin_session", "session_id": "gateway-session-mismatch", "payload": {"context_id": "hwnd:202"}})");
    if (mismatchBeginResponse.find(R"("name": "error")") == std::string::npos) {
        return fail("gateway begin_session mismatch should error");
    }
    const std::string registerUnavailableResponse = gatewayService.processGatewayCommand(
        R"({"version": 1, "kind": "command", "name": "register_active_context", "session_id": "rendezvous", "payload": {"context_id": "hwnd:303", "edit_session_ready": false}})");
    if (registerUnavailableResponse.find(R"("name": "ack")") == std::string::npos) {
        return fail("gateway register_active_context false readiness did not ack");
    }
    const std::string unavailableBeginResponse = gatewayService.processGatewayCommand(
        R"({"version": 1, "kind": "command", "name": "begin_session", "session_id": "gateway-session-unavailable", "payload": {"context_id": "hwnd:303"}})");
    if (unavailableBeginResponse.find(R"("name": "error")") == std::string::npos) {
        return fail("gateway begin_session should error when edit session unavailable");
    }
    const std::string restoreReadyResponse = gatewayService.processGatewayCommand(
        R"({"version": 1, "kind": "command", "name": "register_active_context", "session_id": "rendezvous", "payload": {"context_id": "hwnd:101", "edit_session_ready": true}})");
    if (restoreReadyResponse.find(R"("name": "ack")") == std::string::npos) {
        return fail("gateway restore register_active_context did not ack");
    }
    const std::string finalResponse = gatewayService.processGatewayCommand(
        R"({"version": 1, "kind": "command", "name": "commit_resolved_final", "session_id": "gateway-session", "payload": {"text": "gateway final"}})");
    if (finalResponse.find(R"("name": "ack")") == std::string::npos) {
        return fail("gateway final commit did not ack");
    }
    const std::string cancelAfterCommitResponse = gatewayService.processGatewayCommand(
        R"({"version": 1, "kind": "command", "name": "cancel_session", "session_id": "gateway-session", "payload": {"reason": "late_cancel"}})");
    if (cancelAfterCommitResponse.find(R"("name": "error")") == std::string::npos) {
        return fail("gateway cancel after commit should error");
    }
    if (gatewayService.writerOwner() != WriterOwner::Tip) {
        return fail("gateway command path should preserve Tip writer ownership");
    }

    TipTextService gatewayPipeService;
    TipGatewayServer gatewayServer;
    if (!gatewayServer.bindEndpoint(kGatewayPipeName)) {
        return fail("gateway server bindEndpoint failed");
    }
    std::thread controlServer([&]() {
        if (!gatewayServer.serveClients(gatewayPipeService, 9)) {
            std::cerr << "gateway serveClients failed\n";
            std::exit(EXIT_FAILURE);
        }
    });
    const std::string gatewayBegin = gatewayRoundTrip(
        kGatewayPipeName,
        R"({"version": 1, "kind": "command", "name": "register_active_context", "session_id": "rendezvous", "payload": {"context_id": "hwnd:77"}})");
    if (gatewayBegin.find(R"("name": "ack")") == std::string::npos) {
        return fail("gateway server register_active_context did not ack");
    }
    const std::string gatewayQuery = gatewayRoundTrip(
        kGatewayPipeName,
        R"({"version": 1, "kind": "command", "name": "query_active_context", "session_id": "rendezvous", "payload": {}})");
    if (gatewayQuery.find(R"("active_context_id": "hwnd:77")") == std::string::npos ||
        gatewayQuery.find(R"("edit_session_ready": true)") == std::string::npos) {
        return fail("gateway server query_active_context did not report active context");
    }
    const std::string gatewayBeginSession = gatewayRoundTrip(
        kGatewayPipeName,
        R"({"version": 1, "kind": "command", "name": "begin_session", "session_id": "pipe-session", "payload": {"context_id": "hwnd:77"}})");
    if (gatewayBeginSession.find(R"("name": "ack")") == std::string::npos) {
        return fail("gateway server begin_session did not ack");
    }
    const std::string gatewayInterim = gatewayRoundTrip(
        kGatewayPipeName,
        R"({"version": 1, "kind": "command", "name": "interim", "session_id": "pipe-session", "payload": {"text": "pipe interim"}})");
    if (gatewayInterim.find(R"("name": "ack")") == std::string::npos) {
        return fail("gateway server interim did not ack");
    }
    const std::string gatewayCancel = gatewayRoundTrip(
        kGatewayPipeName,
        R"({"version": 1, "kind": "command", "name": "cancel_session", "session_id": "pipe-session", "payload": {"reason": "test_cancel"}})");
    if (gatewayCancel.find(R"("name": "ack")") == std::string::npos) {
        return fail("gateway server cancel did not ack");
    }
    const std::string gatewayBegin2 = gatewayRoundTrip(
        kGatewayPipeName,
        R"({"version": 1, "kind": "command", "name": "begin_session", "session_id": "pipe-session-2", "payload": {"context_id": "hwnd:88"}})");
    if (gatewayBegin2.find(R"("name": "error")") == std::string::npos) {
        return fail("gateway server second begin_session should fail when rendezvous context mismatches");
    }
    const std::string gatewayRegister2 = gatewayRoundTrip(
        kGatewayPipeName,
        R"({"version": 1, "kind": "command", "name": "register_active_context", "session_id": "rendezvous", "payload": {"context_id": "hwnd:88"}})");
    if (gatewayRegister2.find(R"("name": "ack")") == std::string::npos) {
        return fail("gateway server second register_active_context did not ack");
    }
    const std::string gatewayBegin3 = gatewayRoundTrip(
        kGatewayPipeName,
        R"({"version": 1, "kind": "command", "name": "begin_session", "session_id": "pipe-session-2", "payload": {"context_id": "hwnd:88"}})");
    if (gatewayBegin3.find(R"("name": "ack")") == std::string::npos) {
        return fail("gateway server third begin_session did not ack");
    }
    const std::string gatewayCommit = gatewayRoundTrip(
        kGatewayPipeName,
        R"({"version": 1, "kind": "command", "name": "commit_resolved_final", "session_id": "pipe-session-2", "payload": {"text": "pipe final"}})");
    if (gatewayCommit.find(R"("name": "ack")") == std::string::npos) {
        return fail("gateway server final commit did not ack");
    }
    const std::string invalidBegin = gatewayPipeService.processGatewayCommand(
        R"({"version": 1, "kind": "command", "name": "begin_session", "session_id": "bad-session", "payload": {"context_id": "foreground:unknown"}})");
    if (invalidBegin.find(R"("name": "error")") == std::string::npos) {
        return fail("invalid gateway begin_session should error");
    }
    controlServer.join();
    if (gatewayPipeService.writerOwner() != WriterOwner::Tip) {
        return fail("gateway server path should preserve Tip writer ownership");
    }
    server.join();
    return EXIT_SUCCESS;
}
