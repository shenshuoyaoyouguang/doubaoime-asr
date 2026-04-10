#include <Windows.h>

#include <cstdlib>
#include <iostream>
#include <string>

#include "TipGatewayServer.h"
#include "TipTextService.h"

int wmain(int argc, wchar_t** argv) {
    if (argc < 2) {
        std::wcerr << L"usage: native_tip_gateway_host <pipe-name> [max-clients] [active-context-id] [edit-session-ready]\n";
        return EXIT_FAILURE;
    }

    std::wstring pipeName = argv[1];
    std::size_t maxClients = 16;
    if (argc >= 3) {
        try {
            maxClients = static_cast<std::size_t>(std::stoul(argv[2]));
        } catch (...) {
            std::wcerr << L"invalid max-clients\n";
            return EXIT_FAILURE;
        }
    }
    std::wstring activeContextId;
    bool editSessionReady = true;
    if (argc >= 4) {
        activeContextId = argv[3];
    }
    if (argc >= 5) {
        editSessionReady = std::wstring(argv[4]) != L"0";
    }

    native_tip::TipTextService service;
    native_tip::TipGatewayServer server;
    if (!activeContextId.empty()) {
        const std::string command = std::string("{\"version\": 1, \"kind\": \"command\", \"name\": \"register_active_context\", \"session_id\": \"rendezvous\", \"payload\": {\"context_id\": \"");
        const int size = WideCharToMultiByte(CP_UTF8, 0, activeContextId.c_str(), -1, nullptr, 0, nullptr, nullptr);
        if (size <= 1) {
            std::wcerr << L"registerActiveContext failed\n";
            return EXIT_FAILURE;
        }
        std::string utf8Context(static_cast<std::size_t>(size - 1), '\0');
        WideCharToMultiByte(CP_UTF8, 0, activeContextId.c_str(), -1, utf8Context.data(), size - 1, nullptr, nullptr);
        const std::string response = service.processGatewayCommand(
            command + utf8Context + "\", \"edit_session_ready\": " + (editSessionReady ? "true" : "false") + "}}}");
        if (response.find("\"name\": \"ack\"") == std::string::npos) {
            std::wcerr << L"registerActiveContext failed\n";
            return EXIT_FAILURE;
        }
    }
    if (!server.bindEndpoint(pipeName)) {
        std::wcerr << L"bindEndpoint failed\n";
        return EXIT_FAILURE;
    }
    if (!server.serveClients(service, maxClients)) {
        std::wcerr << L"serveClients failed\n";
        return EXIT_FAILURE;
    }
    return EXIT_SUCCESS;
}
