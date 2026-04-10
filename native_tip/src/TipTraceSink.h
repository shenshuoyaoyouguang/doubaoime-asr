#pragma once

#include <string>
#include <vector>

namespace native_tip {

class TipTraceSink {
public:
    void append(const std::wstring& message);
    std::size_t size() const;
    std::wstring last() const;

private:
    static constexpr std::size_t kMaxEntries = 128;
    std::vector<std::wstring> entries_;
};

}  // namespace native_tip

