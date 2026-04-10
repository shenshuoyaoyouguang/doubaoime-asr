#include "TipGatewayServer.h"

#include <Windows.h>

#include <string>

#include "TipTextService.h"

namespace native_tip {

namespace {

constexpr DWORD kPipeBufferSize = 4096;

}  // namespace

bool TipGatewayServer::bindEndpoint(const std::wstring& endpoint) {
    endpoint_ = endpoint;
    return !endpoint_.empty();
}

bool TipGatewayServer::serveClients(TipTextService& service, std::size_t maxClients) {
    if (endpoint_.empty()) {
        return false;
    }
    for (std::size_t clientIndex = 0; clientIndex < maxClients; ++clientIndex) {
        HANDLE pipe = CreateNamedPipeW(
            endpoint_.c_str(),
            PIPE_ACCESS_DUPLEX,
            PIPE_TYPE_BYTE | PIPE_READMODE_BYTE | PIPE_WAIT,
            1,
            kPipeBufferSize,
            kPipeBufferSize,
            0,
            nullptr);
        if (pipe == INVALID_HANDLE_VALUE) {
            return false;
        }
        if (!ConnectNamedPipe(pipe, nullptr) && GetLastError() != ERROR_PIPE_CONNECTED) {
            CloseHandle(pipe);
            return false;
        }
        const bool ok = handleClient(pipe, service);
        FlushFileBuffers(pipe);
        DisconnectNamedPipe(pipe);
        CloseHandle(pipe);
        if (!ok) {
            return false;
        }
    }
    return true;
}

bool TipGatewayServer::hasEndpoint() const {
    return !endpoint_.empty();
}

const std::wstring& TipGatewayServer::endpoint() const {
    return endpoint_;
}

bool TipGatewayServer::handleClient(void* pipeHandle, TipTextService& service) {
    auto* handle = static_cast<HANDLE>(pipeHandle);
    std::string buffer;
    char chunk[256];
    while (true) {
        DWORD bytesRead = 0;
        const BOOL ok = ReadFile(handle, chunk, sizeof(chunk), &bytesRead, nullptr);
        if (!ok) {
            const DWORD error = GetLastError();
            return error == ERROR_BROKEN_PIPE || error == ERROR_NO_DATA;
        }
        if (bytesRead == 0) {
            return true;
        }
        buffer.append(chunk, chunk + bytesRead);
        std::size_t newline = buffer.find('\n');
        while (newline != std::string::npos) {
            const std::string line = buffer.substr(0, newline);
            buffer.erase(0, newline + 1);
            if (!line.empty()) {
                if (!writeLine(handle, service.processGatewayCommand(line))) {
                    return false;
                }
            }
            newline = buffer.find('\n');
        }
    }
}

bool TipGatewayServer::writeLine(void* pipeHandle, const std::string& line) {
    auto* handle = static_cast<HANDLE>(pipeHandle);
    const std::string payload = line + "\n";
    DWORD bytesWritten = 0;
    const BOOL ok = WriteFile(
        handle,
        payload.data(),
        static_cast<DWORD>(payload.size()),
        &bytesWritten,
        nullptr);
    return ok == TRUE && bytesWritten == payload.size();
}

}  // namespace native_tip
