# doubaoime-asr

豆包输入法语音识别 Python 客户端。

## 免责声明

本项目通过对安卓豆包输入法客户端通信协议分析并参考客户端代码实现，**非官方提供的 API**。

- 本项目仅供学习和研究目的
- 不保证未来的可用性和稳定性
- 服务端协议可能随时变更导致功能失效

## 安装

```bash
# 从本地安装
git clone https://github.com/starccy/doubaoime-asr.git
cd doubaoime-asr
pip install -e .

# 或从 Git 仓库安装
pip install git+https://github.com/starccy/doubaoime-asr.git
```

### 系统依赖

本项目依赖 Opus 音频编解码库，需要先安装系统库：

```bash
# Debian/Ubuntu
sudo apt install libopus0

# Arch Linux
sudo pacman -S opus

# macOS
brew install opus
```

## 快速开始

### 基本用法

```python
import asyncio
from doubaoime_asr import transcribe, ASRConfig

async def main():
    # 配置（首次运行会自动注册设备，并将凭据保存到指定文件）
    config = ASRConfig(credential_path="./credentials.json")

    # 识别音频文件
    result = await transcribe("audio.wav", config=config)
    print(f"识别结果: {result}")

asyncio.run(main())
```

### 流式识别

如果需要获取中间结果或更详细的状态信息，可以使用 `transcribe_stream`：

```python
import asyncio
from doubaoime_asr import transcribe_stream, ASRConfig, ResponseType

async def main():
    config = ASRConfig(credential_path="./credentials.json")

    async for response in transcribe_stream("audio.wav", config=config):
        match response.type:
            case ResponseType.INTERIM_RESULT:
                print(f"[中间结果] {response.text}")
            case ResponseType.FINAL_RESULT:
                print(f"[最终结果] {response.text}")
            case ResponseType.ERROR:
                print(f"[错误] {response.error_msg}")

asyncio.run(main())
```

### 实时麦克风识别

实时语音识别需要配合音频采集库使用，请参考 [examples/mic_realtime.py](examples/mic_realtime.py)。

运行示例需要安装额外依赖：

```bash
pip install sounddevice numpy
# 或
pip install doubaoime-asr[examples]
```

## Windows 全局语音输入代理

如果你想得到“随时可用、按住热键就能开始说话”的全局版，可以直接启动内置的 Windows 全局语音输入代理。

这一版的原则是：**托盘常驻 + 全局热键 + 当前焦点输入框注入。**

当前架构：

- `Controller`：常驻后台，负责全局热键、状态和启动/停止识别进程
- `Worker`：独立识别进程，复用实时识别主干，只负责采音和输出识别事件

### 安装桌面依赖

```bash
pip install -e ".[desktop]"
```

### 启动代理

```bash
doubao-voice-agent
# 或
python -m doubaoime_asr.agent.stable_main
```

默认行为：

- 平台：仅支持 Windows
- 热键：`F8`
- 交互：按住 `F8` 说话，松开结束
- 默认模式：`inject`
- 默认形态：系统托盘常驻
- 默认效果：识别结果尝试写入当前焦点输入框
- 可选调试模式：`recognize`

### 配置文件

首次启动会自动创建：

```text
%APPDATA%/DoubaoVoiceInput/config.json
```

日志文件位置：

```text
%APPDATA%/DoubaoVoiceInput/logs/controller.log
%APPDATA%/DoubaoVoiceInput/logs/workers/worker-*.log
```

默认配置示例：

```json
{
  "hotkey": "f8",
  "microphone_device": null,
  "credential_path": "%APPDATA%/DoubaoVoiceInput/credentials.json",
  "render_debounce_ms": 80
}
```

凭据路径默认策略：

- 优先使用当前工作目录或项目目录下已存在的 `credentials.json`
- 找不到时才回退到 `%APPDATA%/DoubaoVoiceInput/credentials.json`

也可以通过命令行临时覆盖：

```bash
doubao-voice-agent --hotkey f9 --render-debounce-ms 50
doubao-voice-agent --hotkey space
doubao-voice-agent --mode inject
doubao-voice-agent --mode recognize --console
doubao-voice-agent --no-tray --console
```

### 打包为可分发程序

```powershell
./scripts/build_voice_agent.ps1
```

该脚本会：

- 安装 `.[desktop,build]`
- 用 PyInstaller 生成 `dist/doubao-voice-agent/`
- 自动带上 `opus.dll` 及其依赖 DLL

### 已知限制

- 当前实现是“桌面代理”，不是原生 Windows IME / TSF 输入法
- 流式上屏基于键盘注入，适用于大部分普通文本输入框，但不保证所有自绘控件、管理员权限窗口、游戏窗口都兼容
- 当前默认就是全局注入模式；如果需要排障，优先切到 `--mode recognize --console`

### 排障建议

如果程序运行了但没有识别或没有上屏，优先检查：

1. 先用 `python examples/mic_realtime.py` 确认 ASR 本身可用
2. 再用 `doubao-voice-agent --mode recognize --console` 观察控制台状态输出
3. 查看 `%APPDATA%/DoubaoVoiceInput/logs/controller.log`
4. 如果 Controller 已触发热键，再看 `%APPDATA%/DoubaoVoiceInput/logs/workers/` 下最新的 worker 日志

## API 参考

### transcribe

非流式语音识别，直接返回最终结果。

```python
async def transcribe(
    audio: str | Path | bytes,
    *,
    config: ASRConfig | None = None,
    on_interim: Callable[[str], None] | None = None,
    realtime: bool = False,
) -> str
```

参数：
- `audio`: 音频文件路径或 PCM 字节数据
- `config`: ASR 配置
- `on_interim`: 中间结果回调
- `realtime`: 是否模拟实时发送（每个音频数据帧之间加入固定的发送延迟）
    - `True`: 模拟实时发送，加入固定的延迟，表现得更像正常的客户端，但会增加整体识别时间
    - `False`: 尽可能快地发送所有数据帧，整体识别时间更短（貌似也不会被风控）

### transcribe_stream

流式语音识别，返回 `ASRResponse` 异步迭代器。

```python
async def transcribe_stream(
    audio: str | Path | bytes,
    *,
    config: ASRConfig | None = None,
    realtime: bool = False,
) -> AsyncIterator[ASRResponse]
```

### transcribe_realtime

实时流式语音识别，接收 PCM 音频数据的异步迭代器。

```python
async def transcribe_realtime(
    audio_source: AsyncIterator[bytes],
    *,
    config: ASRConfig | None = None,
) -> AsyncIterator[ASRResponse]
```

### ASRConfig

配置类，支持以下主要参数：

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `credential_path` | str | None | 凭据缓存文件路径 |
| `device_id` | str | None | 设备 ID（空则自动注册） |
| `token` | str | None | 认证 Token（空则自动获取） |
| `sample_rate` | int | 16000 | 采样率 |
| `channels` | int | 1 | 声道数 |
| `enable_punctuation` | bool | True | 是否启用标点 |

### ResponseType

响应类型枚举：

| 类型 | 说明 |
|------|------|
| `TASK_STARTED` | 任务已启动 |
| `SESSION_STARTED` | 会话已启动 |
| `VAD_START` | 检测到语音开始 |
| `INTERIM_RESULT` | 中间识别结果 |
| `FINAL_RESULT` | 最终识别结果 |
| `SESSION_FINISHED` | 会话结束 |
| `ERROR` | 错误 |

## 凭据管理

首次使用时会自动向服务器注册虚拟设备（设备参数定义在 `constants.py` 的 `DEFAULT_DEVICE_CONFIG` 中）并获取认证 Token。

推荐指定 `credential_path` 参数，凭据会自动缓存到文件，避免重复注册：

```python
config = ASRConfig(credential_path="~/.config/doubaoime-asr/credentials.json")
```
