from __future__ import annotations

from doubaoime_asr.agent.config import AgentConfig
from doubaoime_asr.agent.config_update_plan import (
    build_config_update_plan,
    polisher_config_changed,
)


def test_build_config_update_plan_detects_hotkey_change() -> None:
    old = AgentConfig(hotkey="f8", hotkey_vk=0x77, hotkey_display="F8")
    new = AgentConfig(hotkey="f9", hotkey_vk=0x78, hotkey_display="F9")

    plan = build_config_update_plan(old, new)

    assert plan.hotkey_changed is True
    assert plan.worker_changed is False
    assert plan.polisher_changed is False


def test_build_config_update_plan_detects_worker_change() -> None:
    old = AgentConfig(credential_path="a.json", microphone_device="Mic 1")
    new = AgentConfig(credential_path="b.json", microphone_device="Mic 1")

    plan = build_config_update_plan(old, new)

    assert plan.hotkey_changed is False
    assert plan.worker_changed is True
    assert plan.polisher_changed is False


def test_polisher_config_changed_detects_prompt_related_updates() -> None:
    old = AgentConfig(polish_mode="ollama", ollama_prompt_template="old")
    new = AgentConfig(polish_mode="ollama", ollama_prompt_template="new")

    assert polisher_config_changed(old, new) is True


def test_build_config_update_plan_can_report_multiple_changes() -> None:
    old = AgentConfig(
        hotkey="f8",
        hotkey_vk=0x77,
        hotkey_display="F8",
        microphone_device="Mic 1",
        polish_mode="off",
    )
    new = AgentConfig(
        hotkey="f9",
        hotkey_vk=0x78,
        hotkey_display="F9",
        microphone_device="Mic 2",
        polish_mode="light",
    )

    plan = build_config_update_plan(old, new)

    assert plan.hotkey_changed is True
    assert plan.worker_changed is True
    assert plan.polisher_changed is True
