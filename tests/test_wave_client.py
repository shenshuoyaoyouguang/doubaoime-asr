import time

from doubaoime_asr.wave_client import WaveSession


def test_wave_session_roundtrip_preserves_bytes():
    session = WaveSession(
        ticket="ticket",
        ticket_long="ticket-long",
        encryption_key=b"\x01\x02",
        client_random=b"\x03\x04",
        server_random=b"\x05\x06",
        shared_key=b"\x07\x08",
        ticket_exp=60,
        ticket_long_exp=120,
        expires_at=time.time() + 30,
    )

    encoded = session.to_dict()
    restored = WaveSession.from_dict(encoded)

    assert restored == session


def test_wave_session_is_expired_uses_expires_at():
    expired = WaveSession(
        ticket="ticket",
        ticket_long="ticket-long",
        encryption_key=b"\x01",
        client_random=b"\x02",
        server_random=b"\x03",
        shared_key=b"\x04",
        ticket_exp=60,
        ticket_long_exp=120,
        expires_at=time.time() - 1,
    )

    assert expired.is_expired() is True
