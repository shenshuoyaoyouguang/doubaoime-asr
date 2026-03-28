from doubaoime_asr.agent.composition import CompositionSession
from doubaoime_asr.agent.input_injector import FocusTarget, utf16_code_units


class FakeInjector:
    def __init__(self):
        self.calls = []

    def replace_text(self, target, previous_text, new_text):
        self.calls.append((target, previous_text, new_text))


def test_composition_session_replaces_previous_text():
    injector = FakeInjector()
    target = FocusTarget(hwnd=1)
    session = CompositionSession(injector, target)

    session.render_interim("你好")
    session.render_interim("你好啊")
    session.finalize("你好啊。")

    assert injector.calls == [
        (target, "", "你好"),
        (target, "你好", "你好啊"),
        (target, "你好啊", "你好啊。"),
    ]
    assert session.final_text == "你好啊。"


def test_utf16_code_units_counts_surrogates():
    assert utf16_code_units("A") == 1
    assert utf16_code_units("你") == 1
    assert utf16_code_units("🙂") == 2
