#include <windows.h>

#include "Guid.h"

namespace native_tip {

// Spike-phase placeholder: real COM/TIP registration is deferred until protocol/session model is finalized.
bool RegisterTipStub() {
    return kTipServiceClsid != GUID{};
}

}  // namespace native_tip

extern "C" __declspec(dllexport) BOOL __stdcall NativeTipRegisterStub() {
    return native_tip::RegisterTipStub() ? TRUE : FALSE;
}

