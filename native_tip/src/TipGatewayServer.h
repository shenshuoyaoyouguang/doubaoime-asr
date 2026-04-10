#pragma once

#include <cstddef>
#include <string>

namespace native_tip {

class TipTextService;

class TipGatewayServer {
public:
    bool bindEndpoint(const std::wstring& endpoint);
    bool serveClients(TipTextService& service, std::size_t maxClients = 1);
    bool hasEndpoint() const;
    const std::wstring& endpoint() const;

private:
    bool handleClient(void* pipeHandle, TipTextService& service);
    bool writeLine(void* pipeHandle, const std::string& line);

    std::wstring endpoint_;
};

}  // namespace native_tip
