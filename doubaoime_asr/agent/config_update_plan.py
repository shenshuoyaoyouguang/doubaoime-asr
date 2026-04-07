from __future__ import annotations

from dataclasses import dataclass

from .config import AgentConfig


@dataclass(frozen=True, slots=True)
class ConfigUpdatePlan:
    """配置变更对运行时的影响判定。"""

    hotkey_changed: bool
    worker_changed: bool
    polisher_changed: bool


def polisher_config_changed(old_config: AgentConfig, new_config: AgentConfig) -> bool:
    """判断润色相关配置是否变化。"""
    return any(
        (
            old_config.polish_mode != new_config.polish_mode,
            old_config.ollama_base_url != new_config.ollama_base_url,
            old_config.ollama_model != new_config.ollama_model,
            old_config.polish_timeout_ms != new_config.polish_timeout_ms,
            old_config.ollama_warmup_enabled != new_config.ollama_warmup_enabled,
            old_config.ollama_keep_alive != new_config.ollama_keep_alive,
            old_config.ollama_prompt_template != new_config.ollama_prompt_template,
        )
    )


def build_config_update_plan(
    old_config: AgentConfig,
    new_config: AgentConfig,
) -> ConfigUpdatePlan:
    """生成配置变更计划，用于协调器和 compat facade 共享。"""
    return ConfigUpdatePlan(
        hotkey_changed=old_config.effective_hotkey_vk()
        != new_config.effective_hotkey_vk(),
        worker_changed=(
            old_config.credential_path != new_config.credential_path
            or old_config.microphone_device != new_config.microphone_device
        ),
        polisher_changed=polisher_config_changed(old_config, new_config),
    )
