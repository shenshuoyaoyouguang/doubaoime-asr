import ctypes
import sys

import pytest

if sys.platform != "win32":
    pytest.skip("SendInput ABI size is Windows-specific", allow_module_level=True)

from doubaoime_asr.agent.input_injector import INPUT


def test_input_struct_matches_windows_sendinput_size():
    expected = 40 if ctypes.sizeof(ctypes.c_void_p) == 8 else 28
    assert ctypes.sizeof(INPUT) == expected
