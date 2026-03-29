from __future__ import annotations

from dataclasses import dataclass
import ctypes
from functools import lru_cache
import logging
import sys
import threading
import uuid

from .config import CAPTURE_OUTPUT_POLICY_MUTE_SYSTEM_OUTPUT


WINFUNCTYPE = getattr(ctypes, "WINFUNCTYPE", ctypes.CFUNCTYPE)
HRESULT = ctypes.c_long
DWORD = ctypes.c_ulong
BOOL = ctypes.c_int

_CLSCTX_ALL = 23
_COINIT_APARTMENTTHREADED = 0x2
_ERENDER = 0
_EMULTIMEDIA = 1
_S_OK = 0
_S_FALSE = 1
_RPC_E_CHANGED_MODE = ctypes.c_long(0x80010106).value


class AudioOutputMuteError(RuntimeError):
    """系统输出静音控制失败。"""


@dataclass(slots=True)
class _MutedEndpointState:
    endpoint_id: str
    previous_muted: bool


class GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", ctypes.c_ulong),
        ("Data2", ctypes.c_ushort),
        ("Data3", ctypes.c_ushort),
        ("Data4", ctypes.c_ubyte * 8),
    ]

    @classmethod
    def from_string(cls, value: str) -> "GUID":
        parsed = uuid.UUID(value)
        return cls(
            parsed.time_low,
            parsed.time_mid,
            parsed.time_hi_version,
            (ctypes.c_ubyte * 8)(*parsed.bytes[8:]),
        )


_CLSID_MMDEVICE_ENUMERATOR = GUID.from_string("BCDE0395-E52F-467C-8E3D-C4579291692E")
_IID_IMMDEVICE_ENUMERATOR = GUID.from_string("A95664D2-9614-4F35-A746-DE8DB63617E6")
_IID_IAUDIO_ENDPOINT_VOLUME = GUID.from_string("5CDF2C82-841E-4546-9722-0CF74078229A")


class SystemOutputMuteGuard:
    def __init__(
        self,
        logger: logging.Logger,
        *,
        policy: str,
    ) -> None:
        self._logger = logger
        self._policy = policy
        self._state: _MutedEndpointState | None = None
        self._lock = threading.Lock()

    def configure(self, policy: str) -> None:
        with self._lock:
            self._policy = policy

    def activate(self) -> bool:
        with self._lock:
            policy = self._policy
            state = self._state

        if policy != CAPTURE_OUTPUT_POLICY_MUTE_SYSTEM_OUTPUT or sys.platform != "win32":
            return False
        if state is not None:
            return True

        endpoint_state = _set_default_output_muted(True)
        with self._lock:
            if self._state is None:
                self._state = endpoint_state
        self._logger.info(
            "capture_output_muted endpoint_id=%s previous_muted=%s",
            endpoint_state.endpoint_id,
            endpoint_state.previous_muted,
        )
        return True

    def release(self) -> bool:
        with self._lock:
            state = self._state

        if state is None:
            return False

        _restore_output_mute_state(state)
        with self._lock:
            if self._state == state:
                self._state = None
        self._logger.info(
            "capture_output_restored endpoint_id=%s restored_muted=%s",
            state.endpoint_id,
            state.previous_muted,
        )
        return True


def _set_default_output_muted(muted: bool) -> _MutedEndpointState:
    with _com_scope():
        enumerator = _create_device_enumerator()
        try:
            device = _get_default_render_device(enumerator)
            try:
                endpoint_id = _get_device_id(device)
                volume = _activate_endpoint_volume(device)
                try:
                    previous_muted = _get_endpoint_mute(volume)
                    _set_endpoint_mute(volume, muted)
                    return _MutedEndpointState(endpoint_id=endpoint_id, previous_muted=previous_muted)
                finally:
                    _release_com_ptr(volume)
            finally:
                _release_com_ptr(device)
        finally:
            _release_com_ptr(enumerator)


def _restore_output_mute_state(state: _MutedEndpointState) -> None:
    with _com_scope():
        enumerator = _create_device_enumerator()
        try:
            device = _get_device_by_id(enumerator, state.endpoint_id)
            try:
                volume = _activate_endpoint_volume(device)
                try:
                    _set_endpoint_mute(volume, state.previous_muted)
                finally:
                    _release_com_ptr(volume)
            finally:
                _release_com_ptr(device)
        finally:
            _release_com_ptr(enumerator)


class _com_scope:
    def __enter__(self) -> "_com_scope":
        hr = _ole32().CoInitializeEx(None, _COINIT_APARTMENTTHREADED)
        self._should_uninitialize = hr in (_S_OK, _S_FALSE)
        if hr not in (_S_OK, _S_FALSE, _RPC_E_CHANGED_MODE):
            _raise_hresult(hr, "CoInitializeEx")
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if getattr(self, "_should_uninitialize", False):
            _ole32().CoUninitialize()


@lru_cache(maxsize=1)
def _ole32():
    if sys.platform != "win32":
        raise AudioOutputMuteError("capture output mute is only available on Windows")
    dll = ctypes.OleDLL("ole32")
    dll.CoInitializeEx.argtypes = [ctypes.c_void_p, DWORD]
    dll.CoInitializeEx.restype = HRESULT
    dll.CoUninitialize.argtypes = []
    dll.CoUninitialize.restype = None
    dll.CoCreateInstance.argtypes = [
        ctypes.POINTER(GUID),
        ctypes.c_void_p,
        DWORD,
        ctypes.POINTER(GUID),
        ctypes.POINTER(ctypes.c_void_p),
    ]
    dll.CoCreateInstance.restype = HRESULT
    dll.CoTaskMemFree.argtypes = [ctypes.c_void_p]
    dll.CoTaskMemFree.restype = None
    return dll


def _create_device_enumerator() -> ctypes.c_void_p:
    enumerator = ctypes.c_void_p()
    hr = _ole32().CoCreateInstance(
        ctypes.byref(_CLSID_MMDEVICE_ENUMERATOR),
        None,
        _CLSCTX_ALL,
        ctypes.byref(_IID_IMMDEVICE_ENUMERATOR),
        ctypes.byref(enumerator),
    )
    _raise_hresult(hr, "CoCreateInstance(IMMDeviceEnumerator)")
    return enumerator


def _get_default_render_device(enumerator: ctypes.c_void_p) -> ctypes.c_void_p:
    device = ctypes.c_void_p()
    hr = _call_com_method(
        enumerator,
        4,
        HRESULT,
        (ctypes.c_int, ctypes.c_int, ctypes.POINTER(ctypes.c_void_p)),
        _ERENDER,
        _EMULTIMEDIA,
        ctypes.byref(device),
    )
    _raise_hresult(hr, "IMMDeviceEnumerator.GetDefaultAudioEndpoint")
    return device


def _get_device_by_id(enumerator: ctypes.c_void_p, endpoint_id: str) -> ctypes.c_void_p:
    device = ctypes.c_void_p()
    hr = _call_com_method(
        enumerator,
        5,
        HRESULT,
        (ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_void_p)),
        endpoint_id,
        ctypes.byref(device),
    )
    _raise_hresult(hr, f"IMMDeviceEnumerator.GetDevice({endpoint_id})")
    return device


def _get_device_id(device: ctypes.c_void_p) -> str:
    endpoint_id = ctypes.c_wchar_p()
    hr = _call_com_method(
        device,
        5,
        HRESULT,
        (ctypes.POINTER(ctypes.c_wchar_p),),
        ctypes.byref(endpoint_id),
    )
    _raise_hresult(hr, "IMMDevice.GetId")
    try:
        if not endpoint_id.value:
            raise AudioOutputMuteError("IMMDevice.GetId returned an empty endpoint id")
        return endpoint_id.value
    finally:
        _ole32().CoTaskMemFree(ctypes.cast(endpoint_id, ctypes.c_void_p))


def _activate_endpoint_volume(device: ctypes.c_void_p) -> ctypes.c_void_p:
    volume = ctypes.c_void_p()
    hr = _call_com_method(
        device,
        3,
        HRESULT,
        (ctypes.POINTER(GUID), DWORD, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)),
        ctypes.byref(_IID_IAUDIO_ENDPOINT_VOLUME),
        _CLSCTX_ALL,
        None,
        ctypes.byref(volume),
    )
    _raise_hresult(hr, "IMMDevice.Activate(IAudioEndpointVolume)")
    return volume


def _get_endpoint_mute(volume: ctypes.c_void_p) -> bool:
    muted = BOOL()
    hr = _call_com_method(
        volume,
        15,
        HRESULT,
        (ctypes.POINTER(BOOL),),
        ctypes.byref(muted),
    )
    _raise_hresult(hr, "IAudioEndpointVolume.GetMute")
    return bool(muted.value)


def _set_endpoint_mute(volume: ctypes.c_void_p, muted: bool) -> None:
    hr = _call_com_method(
        volume,
        14,
        HRESULT,
        (BOOL, ctypes.c_void_p),
        BOOL(bool(muted)),
        None,
    )
    _raise_hresult(hr, "IAudioEndpointVolume.SetMute")


def _release_com_ptr(ptr: ctypes.c_void_p) -> None:
    raw = ctypes.cast(ptr, ctypes.c_void_p)
    if not raw.value:
        return
    _call_com_method(raw, 2, ctypes.c_ulong, ())


def _call_com_method(ptr: ctypes.c_void_p, index: int, restype, signature, *args):
    raw = ctypes.cast(ptr, ctypes.c_void_p)
    if not raw.value:
        raise AudioOutputMuteError("COM pointer is null")
    vtable = ctypes.cast(raw, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
    prototype = WINFUNCTYPE(restype, ctypes.c_void_p, *signature)
    method = prototype(vtable[index])
    return method(raw, *args)


def _raise_hresult(hr: int, operation: str) -> None:
    if hr >= 0:
        return
    code = ctypes.c_ulong(hr).value
    raise AudioOutputMuteError(f"{operation} failed with HRESULT 0x{code:08X}")
