# ADR — Native TIP + External Python Service

## Status

Accepted

## Context

当前仓库主路径仍是：

- Python controller / worker 双进程
- worker 通过 JSON 行协议与 controller 通信
- 高风险输入场景依赖 overlay / injection fallback

相关事实：

- `pyproject.toml:40-41`
- `doubaoime_asr/agent/stable_main.py`
- `doubaoime_asr/agent/protocol.py`
- `doubaoime_asr/agent/worker_main.py`
- `doubaoime_asr/agent/session_manager.py`

## Decision

采用：

- **C++ TSF Text Service (TIP)** 作为原生输入主路径
- **Python 外部 ASR service** 作为录音 / ASR / 运行时服务层
- **legacy overlay / injection** 作为显式 fallback

## Why

1. 注入路径在非标准控件下天然不稳
2. TSF/TIP 才能让 interim/final 进入宿主编辑上下文
3. Python 不适合作为 in-proc TSF 主体
4. 当前仓库已有成熟 ASR 与 C++ 构建基础，可复用

## Consequences

### Positive

- 主路径原生化
- fallback 可保留
- brownfield 迁移成本可控

### Negative

- 双栈复杂度上升
- 需要处理 COM / TSF / 注册 / IPC

## Rejected alternatives

### 1. Continue strengthening injection

被拒绝原因：

- 无法根治浏览器/富文本/未知控件一致性问题

### 2. Service-first without TSF spike

被拒绝原因：

- 会先冻结错误的外部边界，延后暴露最硬的 TSF 风险

### 3. TIP-only prototype disconnected from brownfield runtime

被拒绝原因：

- 难以平滑回接现有 Python runtime
- 回滚价值差

