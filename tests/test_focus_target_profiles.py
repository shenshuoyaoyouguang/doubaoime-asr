from doubaoime_asr.agent.input_injector import classify_focus_target


def test_classify_windows_terminal():
    is_terminal, kind = classify_focus_target("WindowsTerminal.exe", "CASCADIA_HOSTING_WINDOW_CLASS", None)

    assert is_terminal is True
    assert kind == "windows_terminal"


def test_classify_console_host():
    is_terminal, kind = classify_focus_target("pwsh.exe", "ConsoleWindowClass", None)

    assert is_terminal is True
    assert kind == "console"
