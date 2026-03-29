# AGENTS.md - doubaoime-asr 代理工作指南

> 面向代理/维护者的项目速查文档。目标是：快速定位模块、按最小改动修复问题、选择正确验证路径。

## 1. 项目定位

`doubaoime-asr` 是一个豆包输入法语音识别 Python 客户端，核心包含两条主线：

1. **ASR 核心库**：负责设备注册、鉴权、音频编码、WebSocket 协议通信、结果解析。
2. **Windows 桌面代理**：负责全局热键、录音会话、浮层预览、文本注入、托盘交互。

### 当前项目重点能力

- 文件音频识别
- 麦克风实时流式识别
- Windows 全局语音输入代理
- NER 命名实体识别

---

## 2. 先判断你在改什么

### A. 如果你在改 ASR 协议/识别链路
优先看：
- `doubaoime_asr/asr.py`
- `doubaoime_asr/config.py`
- `doubaoime_asr/audio.py`
- `doubaoime_asr/device.py`
- `doubaoime_asr/wave_client.py`
- `doubaoime_asr/constants.py`

### B. 如果你在改 Windows 热键/识别会话/注入
优先看：
- `doubaoime_asr/agent/stable_simple_app.py`
- `doubaoime_asr/agent/worker_main.py`
- `doubaoime_asr/agent/config.py`
- `doubaoime_asr/agent/protocol.py`
- `doubaoime_asr/agent/win_keyboard_hook.py`
- `doubaoime_asr/agent/win_hotkey.py`
- `doubaoime_asr/agent/input_injector.py`
- `doubaoime_asr/agent/injection_manager.py`

### C. 如果你在改浮层预览
优先看：
- `doubaoime_asr/agent/overlay_preview.py`
- `doubaoime_asr/agent/overlay_preview_cpp.py`
- `doubaoime_asr/agent/overlay_scheduler.py`
- `overlay_ui/overlay_ui.cpp`
- `overlay_ui/overlay_window.cpp`
- `overlay_ui/overlay_window.h`

### D. 如果你在改设置页/运行时配置
优先看：
- `doubaoime_asr/agent/settings_window.py`
- `doubaoime_asr/agent/config.py`
- `doubaoime_asr/agent/stable_main.py`
- `doubaoime_asr/agent/stable_simple_app.py`

---

## 3. 关键目录与职责

| 路径 | 职责 | 修改时注意 |
|---|---|---|
| `doubaoime_asr/` | 核心 ASR 库 | 尽量保持协议与配置兼容 |
| `doubaoime_asr/agent/` | Windows 桌面代理 | 大部分问题都与状态机、会话、注入兼容性有关 |
| `overlay_ui/` | 原生 C++ Overlay UI | 改完最好重新构建 `overlay_ui.exe` |
| `tests/` | pytest 测试 | 优先补回归测试，不做大而泛的重写 |
| `scripts/` | 构建脚本 | Windows 打包与原生 UI 构建入口 |
| `examples/` | 示例脚本 | 只在接口行为变化时同步更新 |

---

## 4. 高频文件索引

| 文件 | 说明 |
|---|---|
| `doubaoime_asr/asr.py` | `DoubaoASR` 主入口；`transcribe` / `transcribe_stream` / `transcribe_realtime` |
| `doubaoime_asr/config.py` | `ASRConfig`、凭据与 session 配置 |
| `doubaoime_asr/device.py` | 设备注册与 token 获取 |
| `doubaoime_asr/wave_client.py` | Wave 加密协议（ECDH + HKDF + ChaCha20） |
| `doubaoime_asr/agent/stable_simple_app.py` | Controller 主状态机，排查桌面代理问题的第一入口 |
| `doubaoime_asr/agent/worker_main.py` | Worker 识别子进程 |
| `doubaoime_asr/agent/input_injector.py` | SendInput/窗口焦点/终端识别 |
| `doubaoime_asr/agent/injection_manager.py` | 注入策略编排（UIA / WM_PASTE / 剪贴板 / 终端） |
| `doubaoime_asr/agent/overlay_preview.py` | Python 侧浮层包装与 fallback |
| `doubaoime_asr/agent/overlay_scheduler.py` | 浮层渲染节流与调度 |
| `doubaoime_asr/agent/settings_window.py` | 设置窗口与配置保存 |

---

## 5. 常见任务 -> 应该改哪里

| 任务 | 首选入口 |
|---|---|
| 热键不生效/切换后异常 | `stable_simple_app.py` + `win_keyboard_hook.py` + `win_hotkey.py` |
| 识别进程退出/重启异常 | `stable_simple_app.py` + `worker_main.py` + `protocol.py` |
| 文本注入失败 | `input_injector.py` + `injection_manager.py` + `clipboard_fallback.py` |
| 终端上屏异常 | `input_injector.py` + `injection_manager.py` |
| 浮层显示卡顿/闪烁/fallback 异常 | `overlay_preview.py` + `overlay_scheduler.py` + `overlay_ui/*` |
| 设置保存后运行时不一致 | `settings_window.py` + `agent/config.py` + `stable_simple_app.py` |
| 凭据/token 失效 | `config.py` + `device.py` + `sami.py` |
| 音频编码/采样率问题 | `audio.py` + `asr.py` |

---

## 6. 开发与运行命令

### 安装

```bash
pip install -e .
pip install -e ".[desktop]"
pip install -e ".[dev]"
```

### 运行

```bash
python examples/file_transcribe.py
python examples/mic_realtime.py
python -m doubaoime_asr.agent.stable_main
# 或
# doubao-voice-agent --mode recognize --console
```

### 测试

```bash
pytest tests/
pytest tests/test_runtime.py -v
```

### 构建

```powershell
./scripts/build_overlay_ui.ps1
./scripts/build_voice_agent.ps1
```

---

## 7. 修改原则

- 优先做**小而可回滚**的修改。
- 优先复用现有模块，不新增依赖。
- 桌面代理问题优先排查：
  - session 状态是否正确
  - worker 事件是否串台
  - 焦点目标是否变化
  - 注入策略是否选错
  - overlay 是否只是在 fallback
- 改配置或状态机时，要同时考虑：
  - 运行时对象是否同步更新
  - 失败回滚是否完整
  - listener / worker / overlay / tray 是否会残留旧状态
- 改终端注入时，优先保留异常信息，不要为了“看起来成功”吞掉失败。
- 改原生 overlay 时，关注资源生命周期，不要在动画路径里反复创建昂贵对象。

---

## 8. 验证建议（按改动范围选）

| 改动范围 | 最低验证 |
|---|---|
| 配置模型/CLI | 对应 `tests/test_agent_config.py`、`tests/test_agent_stable_cli.py` |
| 热键/状态机/worker 生命周期 | `tests/test_stable_simple_app.py`、相关 hotkey 测试 |
| 注入策略/终端粘贴 | `tests/test_injection_manager.py`、`tests/test_input_injector.py` |
| Overlay Python 包装层 | `tests/test_overlay_preview_wrapper.py`、`tests/test_overlay_scheduler.py` |
| Overlay C++ | `./scripts/build_overlay_ui.ps1` |
| ASR 核心 | 对应 pytest + 示例脚本冒烟 |

如果没有完整环境，也至少做到：
- 语法/导入检查通过
- 受影响测试通过
- 构建脚本能跑通（如涉及 C++ overlay）

---

## 9. 平台与运行时限制

### Windows 限制

桌面代理仅支持 Windows。涉及以下模块时默认按 Windows 语义处理：
- `doubaoime_asr/agent/*`
- `overlay_ui/*`

### 注入兼容性

文本注入不是对所有控件都 100% 通用：
- 普通编辑控件：优先 direct input / UIA / WM_PASTE
- 终端类控件：需走终端特化策略
- 某些控件只能退回剪贴板方案

### 非官方 API 风险

该项目依赖逆向协议：
- 接口可能随时变化
- token / 设备注册策略可能失效
- 协议层问题优先核查 `constants.py` / `device.py` / `wave_client.py`

---

## 10. 关键运行时信息

| 项目 | 默认值 |
|---|---|
| 凭据缓存 | `%APPDATA%/DoubaoVoiceInput/credentials.json` |
| 日志目录 | `%APPDATA%/DoubaoVoiceInput/logs/` |
| 默认热键 | `Right Ctrl` |
| 采样率 | `16000 Hz` |
| 声道 | 单声道 |
| Opus 帧长 | `20ms` |

### Windows Opus DLL

以下 DLL 需可被加载：
- `opus.dll`
- `libgcc_s_seh-1.dll`
- `libwinpthread-1.dll`

相关逻辑：`doubaoime_asr/_runtime.py`

---

## 11. 文档入口

- `README.md`：用户向项目说明
- `wave_protocol.md`：Wave 协议说明
- `doubaoime_asr/asr.proto`：协议定义

---

## 12. 给代理的最后建议

如果需求含糊，优先先定位到这三层：
1. **配置层**：参数是否正确进入系统
2. **状态层**：controller / worker / overlay 是否状态一致
3. **执行层**：网络、音频、注入、渲染是否真正执行成功

大多数问题都不是“单点函数错误”，而是**配置、状态、资源生命周期**三者不同步。
