#pragma once

#include <string>

namespace native_tip {

class TipServiceClient {
public:
    bool bindEndpoint(const std::wstring& endpoint);
    bool connectBoundEndpoint();
    bool connect(const std::wstring& endpoint);
    bool disconnect();
    bool sendLine(const std::string& line);
    bool readLine(std::string* line);
    bool isConnected() const;
    bool hasEndpoint() const;
    const std::wstring& endpoint() const;

private:
    std::wstring endpoint_;
    std::string readBuffer_;
    bool connected_ = false;
    void* pipeHandle_ = nullptr;
};

}  // namespace native_tip
