"""
Coordinator CLI 模块。

提供命令行参数解析和配置构建功能。
"""
from __future__ import annotations

import argparse

from .config import (
    AgentConfig,
    SUPPORTED_CAPTURE_OUTPUT_POLICIES,
    SUPPORTED_FINAL_COMMIT_SOURCES,
    SUPPORTED_INJECTION_POLICIES,
    SUPPORTED_POLISH_MODES,
    SUPPORTED_STREAMING_TEXT_MODES,
)
from .win_hotkey import normalize_hotkey, vk_from_hotkey, vk_to_display, vk_to_hotkey


__all__ = [
    "build_arg_parser",
    "build_config_from_args",
    "normalize_cli_hotkey",
]


def build_arg_parser() -> argparse.ArgumentParser:
    """构建命令行参数解析器。"""
    parser = argparse.ArgumentParser(description="Doubao 语音输入全局版")
    parser.add_argument(
        "--mode",
        choices=("recognize", "inject"),
        default=argparse.SUPPRESS,
        help="recognize 仅识别；inject 识别后尝试写入当前焦点输入框",
    )
    parser.add_argument("--hotkey", help="覆盖默认热键，例如 right_ctrl / f9 / space")
    parser.add_argument("--mic-device", help="覆盖麦克风设备名称或索引")
    parser.add_argument("--credential-path", help="覆盖凭据文件路径")
    parser.add_argument(
        "--injection-policy",
        choices=SUPPORTED_INJECTION_POLICIES,
        default=argparse.SUPPRESS,
        help="direct_only 仅直接输入；direct_then_clipboard 失败时允许剪贴板回退",
    )
    parser.add_argument(
        "--streaming-text-mode",
        choices=SUPPORTED_STREAMING_TEXT_MODES,
        default=argparse.SUPPRESS,
        help="safe_inline 安全编辑框实时上屏；overlay_only 仅显示浮层",
    )
    parser.add_argument(
        "--final-commit-source",
        choices=SUPPORTED_FINAL_COMMIT_SOURCES,
        default=argparse.SUPPRESS,
        help="polished 提交润色结果（兼容当前行为）；raw 提交原始识别结果",
    )
    parser.add_argument(
        "--capture-output-policy",
        choices=SUPPORTED_CAPTURE_OUTPUT_POLICIES,
        default=argparse.SUPPRESS,
        help="off 保持现状；mute_system_output 在录音期间静音系统输出",
    )
    parser.add_argument(
        "--polish-mode",
        choices=SUPPORTED_POLISH_MODES,
        default=argparse.SUPPRESS,
        help="light 轻量整理（推荐）；off 关闭；ollama 使用本地 Ollama 模型润色最终结果（较慢）",
    )
    parser.add_argument("--ollama-base-url", help="本地 Ollama 服务地址，默认 http://localhost:11434")
    parser.add_argument("--ollama-model", help="本地 Ollama 模型名，为空时仅在唯一模型场景下自动探测")
    parser.add_argument("--polish-timeout-ms", type=int, help="最终结果润色超时毫秒数")
    parser.add_argument("--ollama-keep-alive", help="Ollama 模型保温时长，例如 15m")
    parser.add_argument("--disable-ollama-warmup", action="store_true", help="关闭程序启动后的 Ollama 模型预热")
    parser.add_argument("--render-debounce-ms", type=int, help="流式渲染防抖毫秒数")
    parser.add_argument("--worker-ready-timeout-ms", type=int, help="Worker 热启动就绪超时毫秒数")
    parser.add_argument("--worker-cold-ready-timeout-ms", type=int, help="Worker 冷启动就绪超时毫秒数")
    parser.add_argument("--worker-exit-grace-timeout-ms", type=int, help="Worker 优雅退出等待毫秒数")
    parser.add_argument("--worker-kill-wait-timeout-ms", type=int, help="Worker 强制终止后等待毫秒数")
    parser.add_argument("--console", action="store_true", help="显示控制台输出，便于调试")
    parser.add_argument("--no-tray", action="store_true", help="禁用系统托盘，仅作为前台常驻工具运行")
    return parser


def build_config_from_args(args: argparse.Namespace | None = None) -> AgentConfig:
    """从命令行参数构建配置。"""
    if args is None:
        parser = build_arg_parser()
        args = parser.parse_args()

    config = AgentConfig.load()
    if getattr(args, "mode", None):
        config.mode = args.mode
    if getattr(args, "hotkey", None):
        hotkey = str(args.hotkey)
        hotkey_vk = vk_from_hotkey(hotkey)
        config.hotkey = normalize_cli_hotkey(hotkey_vk)
        config.hotkey_vk = hotkey_vk
        config.hotkey_display = vk_to_display(hotkey_vk)
    if getattr(args, "mic_device", None):
        config.microphone_device = (
            int(args.mic_device)
            if str(args.mic_device).isdigit()
            else args.mic_device
        )
    if getattr(args, "credential_path", None):
        config.credential_path = args.credential_path
    if getattr(args, "injection_policy", None):
        config.injection_policy = args.injection_policy
    if getattr(args, "streaming_text_mode", None):
        config.streaming_text_mode = args.streaming_text_mode
    if getattr(args, "final_commit_source", None):
        config.final_commit_source = args.final_commit_source
    if getattr(args, "capture_output_policy", None):
        config.capture_output_policy = args.capture_output_policy
    if getattr(args, "polish_mode", None):
        config.polish_mode = args.polish_mode
    if getattr(args, "ollama_base_url", None):
        config.ollama_base_url = str(args.ollama_base_url).strip().rstrip("/") or config.ollama_base_url
    if getattr(args, "ollama_model", None) is not None:
        config.ollama_model = str(args.ollama_model).strip()
    if getattr(args, "polish_timeout_ms", None) is not None:
        config.polish_timeout_ms = args.polish_timeout_ms
    if getattr(args, "ollama_keep_alive", None):
        config.ollama_keep_alive = args.ollama_keep_alive
    if getattr(args, "disable_ollama_warmup", False):
        config.ollama_warmup_enabled = False
    if getattr(args, "render_debounce_ms", None) is not None:
        config.render_debounce_ms = args.render_debounce_ms
    if getattr(args, "worker_ready_timeout_ms", None) is not None:
        config.worker_ready_timeout_ms = args.worker_ready_timeout_ms
    if getattr(args, "worker_cold_ready_timeout_ms", None) is not None:
        config.worker_cold_ready_timeout_ms = args.worker_cold_ready_timeout_ms
    if getattr(args, "worker_exit_grace_timeout_ms", None) is not None:
        config.worker_exit_grace_timeout_ms = args.worker_exit_grace_timeout_ms
    if getattr(args, "worker_kill_wait_timeout_ms", None) is not None:
        config.worker_kill_wait_timeout_ms = args.worker_kill_wait_timeout_ms
    return config


def normalize_cli_hotkey(hotkey_vk: int) -> str:
    """规范化 CLI 热键。"""
    return vk_to_hotkey(hotkey_vk) or normalize_hotkey(vk_to_display(hotkey_vk))