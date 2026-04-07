from doubaoime_asr.agent.input_injector import classify_focus_target, classify_text_input_profile


def test_classify_windows_terminal():
    is_terminal, kind = classify_focus_target("WindowsTerminal.exe", "CASCADIA_HOSTING_WINDOW_CLASS", None)

    assert is_terminal is True
    assert kind == "windows_terminal"


def test_classify_console_host():
    is_terminal, kind = classify_focus_target("pwsh.exe", "ConsoleWindowClass", None)

    assert is_terminal is True
    assert kind == "console"


def test_classify_notepad_as_plain_editor():
    profile = classify_text_input_profile("notepad.exe", "Notepad", "Edit", is_terminal=False)

    assert profile == "plain_editor"


def test_classify_browser_editable_profile():
    profile = classify_text_input_profile("chrome.exe", "Chrome_WidgetWin_1", "Chrome_RenderWidgetHostHWND", is_terminal=False)

    assert profile == "browser_editable"


def test_classify_unknown_profile():
    profile = classify_text_input_profile("slack.exe", "SlackWindowClass", "SomeCustomInputClass", is_terminal=False)

    assert profile == "unknown"


def test_terminal_profile_overrides_other_signals():
    profile = classify_text_input_profile("notepad.exe", "Notepad", "Edit", is_terminal=True)

    assert profile == "terminal"
