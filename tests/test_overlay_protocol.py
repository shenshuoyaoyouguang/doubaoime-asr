from doubaoime_asr.agent.overlay_protocol import decode_overlay_event, encode_overlay_command


def test_encode_overlay_command_preserves_utf8_text():
    line = encode_overlay_command("show", text='你好 "Doubao"')

    assert line == '{"cmd": "show", "text": "你好 \\"Doubao\\""}'


def test_decode_overlay_event_requires_object():
    event = decode_overlay_event('{"event":"ready"}')

    assert event["event"] == "ready"


def test_encode_overlay_command_supports_overlay_config_payload():
    line = encode_overlay_command("configure", font_size="16", opacity_percent="90")

    assert line == '{"cmd": "configure", "font_size": "16", "opacity_percent": "90"}'


def test_encode_overlay_command_supports_show_seq_and_kind():
    line = encode_overlay_command("show", text="你好", seq="12", kind="interim", stable_prefix_utf16_len="1")

    assert line == '{"cmd": "show", "text": "你好", "seq": "12", "kind": "interim", "stable_prefix_utf16_len": "1"}'


def test_encode_overlay_command_supports_recording_hud_payload():
    line = encode_overlay_command(
        "show",
        text="正在聆听…",
        seq="2",
        kind="listening",
        stable_prefix_utf16_len="5",
        show_microphone="1",
        level="0.2500",
    )

    assert line == (
        '{"cmd": "show", "text": "正在聆听…", "seq": "2", "kind": "listening", '
        '"stable_prefix_utf16_len": "5", "show_microphone": "1", "level": "0.2500"}'
    )
