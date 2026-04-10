#include "TipServiceClient.h"

#include <Windows.h>

namespace native_tip {

bool TipServiceClient::bindEndpoint(const std::wstring& endpoint) {
    endpoint_ = endpoint;
    return !endpoint_.empty();
}

bool TipServiceClient::connectBoundEndpoint() {
    return connect(endpoint_);
}

bool TipServiceClient::connect(const std::wstring& endpoint) {
    if (!endpoint.empty()) {
        endpoint_ = endpoint;
    }
    if (endpoint_.empty()) {
        connected_ = false;
        return false;
    }
    HANDLE handle = INVALID_HANDLE_VALUE;
    for (int attempt = 0; attempt < 20; ++attempt) {
        handle = CreateFileW(
            endpoint_.c_str(),
            GENERIC_READ | GENERIC_WRITE,
            0,
            nullptr,
            OPEN_EXISTING,
            0,
            nullptr);
        if (handle != INVALID_HANDLE_VALUE) {
            break;
        }
        Sleep(25);
    }
    if (handle == INVALID_HANDLE_VALUE) {
        connected_ = false;
        pipeHandle_ = nullptr;
        return false;
    }
    pipeHandle_ = handle;
    connected_ = true;
    return connected_;
}

bool TipServiceClient::disconnect() {
    if (pipeHandle_ != nullptr) {
        CloseHandle(static_cast<HANDLE>(pipeHandle_));
        pipeHandle_ = nullptr;
    }
    readBuffer_.clear();
    connected_ = false;
    return true;
}

bool TipServiceClient::sendLine(const std::string& line) {
    if (!connected_ || pipeHandle_ == nullptr) {
        return false;
    }
    const std::string payload = line + "\n";
    DWORD bytesWritten = 0;
    const BOOL ok = WriteFile(
        static_cast<HANDLE>(pipeHandle_),
        payload.data(),
        static_cast<DWORD>(payload.size()),
        &bytesWritten,
        nullptr);
    return ok == TRUE && bytesWritten == payload.size();
}

bool TipServiceClient::readLine(std::string* line) {
    if (line == nullptr || !connected_ || pipeHandle_ == nullptr) {
        return false;
    }
    const std::size_t existingNewline = readBuffer_.find('\n');
    if (existingNewline != std::string::npos) {
        *line = readBuffer_.substr(0, existingNewline);
        readBuffer_.erase(0, existingNewline + 1);
        return true;
    }
    char buffer[256];
    while (true) {
        DWORD bytesRead = 0;
        const BOOL ok = ReadFile(
            static_cast<HANDLE>(pipeHandle_),
            buffer,
            sizeof(buffer),
            &bytesRead,
            nullptr);
        if (!ok || bytesRead == 0) {
            return false;
        }
        readBuffer_.append(buffer, buffer + bytesRead);
        const std::size_t newline = readBuffer_.find('\n');
        if (newline != std::string::npos) {
            *line = readBuffer_.substr(0, newline);
            readBuffer_.erase(0, newline + 1);
            return true;
        }
    }
}

bool TipServiceClient::isConnected() const {
    return connected_;
}

bool TipServiceClient::hasEndpoint() const {
    return !endpoint_.empty();
}

const std::wstring& TipServiceClient::endpoint() const {
    return endpoint_;
}

}  // namespace native_tip
