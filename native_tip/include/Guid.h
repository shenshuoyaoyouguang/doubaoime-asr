#pragma once

#include <guiddef.h>

namespace native_tip {

// Placeholder GUIDs for the spike phase; replace with production GUIDs before registration logic is finalized.
inline constexpr GUID kTipServiceClsid{
    0x5f7f94a1, 0x8f85, 0x46a8, {0x8f, 0x1b, 0x4a, 0x87, 0x51, 0x1d, 0x49, 0x73}
};

inline constexpr GUID kTipProfileGuid{
    0xd3c0f02f, 0x8f84, 0x4d9f, {0x88, 0x44, 0x8d, 0xa5, 0xbe, 0xda, 0xa1, 0xf6}
};

}  // namespace native_tip

