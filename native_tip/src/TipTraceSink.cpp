#include "TipTraceSink.h"

namespace native_tip {

void TipTraceSink::append(const std::wstring& message) {
    if (entries_.size() == kMaxEntries) {
        entries_.erase(entries_.begin());
    }
    entries_.push_back(message);
}

std::size_t TipTraceSink::size() const {
    return entries_.size();
}

std::wstring TipTraceSink::last() const {
    return entries_.empty() ? std::wstring{} : entries_.back();
}

}  // namespace native_tip

