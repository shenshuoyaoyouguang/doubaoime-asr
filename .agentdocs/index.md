# 文档目录索引

> doubaoime-asr 项目文档总入口，包含完整的模块索引、配置说明和开发指南。

---

## 文档结构

```
.agentdocs/
├── index.md          # 本文件 - 文档总索引
├── system-prompt.md  # 系统提示词
└── workflow/         # 任务文档
    ├── 260330-voice-input-refactor.md   # 语音输入模块重构
    ├── 260408-refactor-delivery-progress.md  # 全链路重构阶段性交付进展
    ├── 260408-project-architecture-baseline.md  # 项目重构全局基线（阶段版）
    ├── 260408-project-target-architecture.md  # 项目目标架构设计（阶段版）
    ├── 260408-module-refactor-summary.md  # 模块级重构设计与测试证据汇总（阶段版）
    ├── 260408-regression-test-report.md  # 全量回归测试报告（阶段版）
    ├── 260408-architecture-compliance-report.md  # 架构合规校验报告（阶段版）
    ├── 260408-deployment-rollback-plan.md  # 上线部署与回滚预案（阶段版）
    ├── 260408-final-delivery-checklist.md  # 最终交付清单（阶段版）
    ├── 260408-final-acceptance-report.md  # 最终验收报告（阶段版）
    ├── 260408-final-delivery-package-summary.md  # 最终交付包总览（阶段版）
    └── 260408-final-delivery-signoff-summary.md  # 最终交付说明 / 签收版摘要（阶段版）
```

---

## 当前任务文档

| 文档 | 说明 |
|------|------|
| `workflow/260330-voice-input-refactor.md` | 语音输入模块重构计划（6 阶段拆分 Controller）|
| `workflow/260408-refactor-delivery-progress.md` | 全链路重构当前完成面、验证结果与交付物映射 |
| `workflow/260408-project-architecture-baseline.md` | 当前原生架构分层、已完成收口与验证基线 |
| `workflow/260408-project-target-architecture.md` | 目标架构分层、目标原则与剩余差距 |
| `workflow/260408-module-refactor-summary.md` | 模块级重构设计、收口内容与测试证据汇总 |
| `workflow/260408-regression-test-report.md` | 当前阶段定向/全量回归结果与验证范围 |
| `workflow/260408-architecture-compliance-report.md` | 当前重构结果与目标架构原则的合规检查 |
| `workflow/260408-deployment-rollback-plan.md` | 阶段版上线部署与回滚预案 |
| `workflow/260408-final-delivery-checklist.md` | 最终交付包应包含的条目与当前勾选状态 |
| `workflow/260408-final-acceptance-report.md` | 当前阶段的验收结论与后续收口顺序 |
| `workflow/260408-final-delivery-package-summary.md` | 将最终交付要求与现有文档、代码、验证结果做映射 |
| `workflow/260408-final-delivery-signoff-summary.md` | 当前阶段的最终交付说明与签收版摘要 |

---

## 项目定位

`doubaoime-asr` 是一个豆包输入法语音识别 Python 客户端，核心包含两条主线：

1. **ASR 核心库**：负责设备注册、鉴权、音频编码、WebSocket 协议通信、结果解析。
2. **Windows 桌面代理**：负责全局热键、录音会话、浮层预览、文本注入、托盘交互。

### 当前项目重点能力

- 文件音频识别
- 麦克风实时流式识别
- Windows 全局语音输入代理
- NER 命名实体识别
- 文本润色（Ollama 集成）
- 系统音频静音（录音时）

---

## 完整文件索引

### ASR 核心库 (`doubaoime_asr/`)

| 文件 | 说明 |
|---|---|
| `asr.py` | `DoubaoASR` 主入口；`transcribe` / `transcribe_stream` / `transcribe_realtime` |
| `config.py` | `ASRConfig`、凭据与 session 配置 |
| `audio.py` | Opus 音频编码处理 |
| `device.py` | 设备注册与 token 获取 |
| `wave_client.py` | Wave 加密协议（ECDH + HKDF + ChaCha20） |
| `constants.py` | 常量定义（设备配置、API 端点等） |
| `ner.py` | NER 命名实体识别接口 |
| `sami.py` | Sami 鉴权服务接口 |
| `_runtime.py` | Opus DLL 加载与运行时依赖检查 |
| `asr.proto` | Protobuf 协议定义 |
| `asr_pb2.py` | Protobuf 编译产物 |
| `__init__.py` | 包入口，导出 `DoubaoASR`、`ASRConfig`、`NER` 等 |

### Agent 模块 (`doubaoime_asr/agent/`)

#### 入口与状态机

| 文件 | 说明 |
|---|---|
| `stable_main.py` | 稳定版入口；支持 `--worker` 模式和控制器模式 |
| `stable_simple_app.py` | Controller 主状态机，排查桌面代理问题的第一入口 |
| `worker_main.py` | Worker 识别子进程 |
| `stable_realtime.py` | 麦克风预缓冲采集，优化启动延迟 |
| `main.py` | 旧版入口（已弃用，调用 `app.py`） |
| `app.py` | 旧版实现（已弃用，基于 pynput） |

#### 文本注入

| 文件 | 说明 |
|---|---|
| `injection_manager.py` | 注入策略编排（UIA / WM_PASTE / 剪贴板 / 终端） |
| `input_injector.py` | SendInput/窗口焦点/终端识别 |
| `clipboard_fallback.py` | 剪贴板操作辅助（快照、恢复、设置） |
| `composition.py` | 文本注入会话管理 |

#### 文本润色

| 文件 | 说明 |
|---|---|
| `text_polisher.py` | 文本润色处理（轻量级本地润色 + Ollama LLM 润色） |

#### Overlay 浮层

| 文件 | 说明 |
|---|---|
| `overlay_preview.py` | Python 侧浮层包装与 fallback |
| `overlay_preview_cpp.py` | C++ overlay 子进程管理 |
| `overlay_protocol.py` | Overlay JSON 通信协议 |
| `overlay_scheduler.py` | 浮层渲染节流与调度（60FPS） |

#### 热键与键盘

| 文件 | 说明 |
|---|---|
| `win_hotkey.py` | Windows 热键 VK 转换 |
| `win_keyboard_hook.py` | 全局键盘钩子 |
| `hotkey.py` | 热键规范化辅助函数 |

#### 系统交互

| 文件 | 说明 |
|---|---|
| `win_audio_output.py` | 系统音频静音守卫（录音时静音系统输出） |
| `win_privileges.py` | Windows 权限检测/提升 |

#### 配置与日志

| 文件 | 说明 |
|---|---|
| `config.py` | `AgentConfig` 配置 |
| `settings_window.py` | 设置窗口 UI 与配置保存 |
| `runtime_logging.py` | 日志初始化 |
| `protocol.py` | Worker 通信协议 |

### 原生 Overlay UI (`overlay_ui/`)

| 文件 | 说明 |
|---|---|
| `overlay_ui.cpp` | 主程序入口 |
| `overlay_window.cpp` | Overlay 窗口实现 |
| `overlay_window.h` | Overlay 窗口头文件 |
| `CMakeLists.txt` | CMake 构建配置 |

---

## 常见任务入口

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
| 文本润色不生效/Ollama 连接失败 | `text_polisher.py` + `agent/config.py`（polish_mode/ollama_*） |
| 录音时系统音频未静音 | `win_audio_output.py` + `stable_simple_app.py` + `agent/config.py`（capture_output_policy） |
| NER 识别失败 | `ner.py` + `config.py` + `sami.py` |
| 流式文本上屏模式问题 | `stable_simple_app.py` + `composition.py` + `agent/config.py`（streaming_text_mode） |
| Overlay 60FPS 渲染卡顿 | `overlay_scheduler.py` + `overlay_ui/*`（overlay_render_fps） |

---

## 开发与运行命令

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

## 验证建议（按改动范围）

| 改动范围 | 最低验证 |
|---|---|
| 配置模型/CLI | `tests/test_agent_config.py`、`tests/test_agent_stable_cli.py` |
| 热键/状态机/worker 生命周期 | `tests/test_stable_simple_app.py`、`tests/test_agent_hotkey.py`、`tests/test_worker_main.py` |
| 注入策略/终端粘贴 | `tests/test_injection_manager.py`、`tests/test_input_injector.py`、`tests/test_focus_target_profiles.py` |
| 组合会话/注入流程 | `tests/test_agent_composition.py` |
| Overlay Python 包装层 | `tests/test_overlay_preview_wrapper.py`、`tests/test_overlay_scheduler.py` |
| Overlay C++ 包装 | `tests/test_overlay_preview_cpp.py`、`tests/test_overlay_protocol.py` |
| Overlay 原生 UI | `./scripts/build_overlay_ui.ps1` |
| 文本润色/Ollama | `tests/test_text_polisher.py` |
| 系统音频静音 | `tests/test_win_audio_output.py` |
| Windows 权限 | `tests/test_win_privileges.py` |
| 设置窗口 | `tests/test_settings_window.py` |
| Wave 协议 | `tests/test_wave_client.py` |
| 运行时加载 | `tests/test_runtime.py` |
| 控制器逻辑 | `tests/test_agent_controller.py` |
| ASR 核心 | 对应 pytest + 示例脚本冒烟（⚠️ `asr.py`、`audio.py`、`device.py` 无测试） |

### 测试覆盖缺失

以下核心模块缺少单元测试，修改时需特别注意：
- `doubaoime_asr/asr.py` - ASR 主入口
- `doubaoime_asr/audio.py` - 音频编码
- `doubaoime_asr/device.py` - 设备注册
- `doubaoime_asr/ner.py` - NER 识别
- `doubaoime_asr/sami.py` - Sami 鉴权
- `doubaoime_asr/agent/win_keyboard_hook.py` - 键盘钩子

---

## 关键运行时信息

### 基础配置

| 项目 | 默认值 |
|---|---|
| 凭据缓存 | `%APPDATA%/DoubaoVoiceInput/credentials.json` |
| 日志目录 | `%APPDATA%/DoubaoVoiceInput/logs/` |
| 默认热键 | `Right Ctrl` |
| 采样率 | `16000 Hz` |
| 声道 | 单声道 |
| Opus 帧长 | `20ms` |

### AgentConfig 配置项

| 配置项 | 默认值 | 说明 |
|---|---|
| `streaming_text_mode` | `safe_inline` | 流式文本显示模式（safe_inline / inline / final_only） |
| `capture_output_policy` | `off` | 录音时静音系统输出（off / mute_system_output） |
| `overlay_render_fps` | `60` | Overlay 渲染帧率 |
| `overlay_font_size` | `14` | Overlay 字体大小 |
| `overlay_max_width` | `620` | Overlay 最大宽度 |
| `overlay_opacity_percent` | `92` | Overlay 透明度 |
| `render_debounce_ms` | `80` | 渲染防抖延迟 |
| `polish_mode` | `light` | 文本润色模式（off / light / ollama） |
| `ollama_base_url` | `http://localhost:11434` | Ollama 服务地址 |
| `ollama_model` | `qwen35-opus-fixed:latest` | Ollama 模型名 |
| `polish_timeout_ms` | `800` | 润色超时时间 |

### Windows Opus DLL

以下 DLL 需可被加载：
- `opus.dll`
- `libgcc_s_seh-1.dll`
- `libwinpthread-1.dll`

相关逻辑：`doubaoime_asr/_runtime.py`

---

## 平台与运行时限制

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

## 修改原则

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
- 改终端注入时，优先保留异常信息，不要为了"看起来成功"吞掉失败。
- 改原生 overlay 时，关注资源生命周期，不要在动画路径里反复创建昂贵对象。

---

## 文档入口

- `README.md`：用户向项目说明
- `wave_protocol.md`：Wave 协议说明
- `doubaoime_asr/asr.proto`：协议定义
- `.agentdocs/index.md`：本文档（完整模块索引）

---

## 问题排查三层法

如果需求含糊，优先先定位到这三层：
1. **配置层**：参数是否正确进入系统
2. **状态层**：controller / worker / overlay 是否状态一致
3. **执行层**：网络、音频、注入、渲染是否真正执行成功

大多数问题都不是"单点函数错误"，而是**配置、状态、资源生命周期**三者不同步。
