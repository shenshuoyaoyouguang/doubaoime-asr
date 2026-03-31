# AGENTS.md - doubaoime-asr 代理速查

> 详细文档见 `.agentdocs/index.md`

## 定位

豆包语音识别客户端：ASR 核心库 + Windows 桌面代理（热键/录音/浮层/注入）

## 改什么看哪里

| 领域 | 入口文件 |
|---|---|
| ASR 协议 | `asr.py` → `wave_client.py` → `device.py` |
| 热键/会话 | `stable_simple_app.py` → `worker_main.py` |
| 注入 | `injection_manager.py` → `input_injector.py` |
| 浮层 | `overlay_scheduler.py` → `overlay_ui/*` |
| 润色/NER | `text_polisher.py` → `ner.py` |
| 设置 | `settings_window.py` → `config.py` |

## 常用命令

```bash
pip install -e ".[desktop,dev]"   # 安装
python -m doubaoime_asr.agent.stable_main  # 运行
pytest tests/                     # 测试
```

## 原则

小修改、可回滚、复用现有模块。排查顺序：配置 → 状态 → 执行。

## 运行时

- 凭据/日志：`%APPDATA%/DoubaoVoiceInput/`
- 热键：`Right Ctrl`
- DLL：`opus.dll` 等 3 个

**详细文档**：`.agentdocs/index.md`