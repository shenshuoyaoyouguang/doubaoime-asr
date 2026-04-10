# Native Bridge Protocol (Draft v1)

## Purpose

定义 `native_tip` 与 Python 外部 ASR service 之间的最小可版本化 IPC 协议。

第一版目标：

- 承载 `start / stop / cancel / ping / exit`
- 承载 `service_ready / status / pong / error / service_exiting`
- 为后续 `interim / final / fallback_reason / writer_owner` 留出演进空间

## Transport

- v1 默认 transport：**Named Pipe**
- 本仓库当前 skeleton 仍允许 stdio 方式做最小 smoke，但实现目标是 pipe
- 编码：UTF-8 JSON Lines

## Envelope

每条消息必须是 JSON object：

```json
{
  "version": 1,
  "kind": "command|event",
  "name": "start",
  "session_id": "uuid-or-null",
  "payload": {}
}
```

## Required fields

- `version`: 正整数，当前固定为 `1`
- `kind`: `command` 或 `event`
- `name`: 取决于 `kind`
- `session_id`: 可选；会话相关消息必须带
- `payload`: object；缺省时视为空 object

## Command set (v1)

- `register_active_context`
- `clear_active_context`
- `query_active_context`
- `start`
- `stop`
- `cancel`
- `ping`
- `exit`

> 注：以上为 service bridge 命令集合；实验性的 native TIP gateway control pipe 额外复用
> `register_active_context / clear_active_context / query_active_context / begin_session / interim / commit_resolved_final / cancel_session`
> 用于 active TIP rendezvous 与控制面验证。

其中 `query_active_context` 返回字段至少包括：
- `active_context_id`
- `edit_session_ready`

### `start`

Payload 建议字段：

- `timeout_ms`
- `trigger_source`：`global_hotkey` / `preserved_key`
- `target_context_id`

### `stop`

表示用户主动结束采集，等待 resolved final。

### `cancel`

表示会话无效化，TIP 必须清理 composition，service 终止本次会话。

### `ping`

健康检查。

### `exit`

请求 service 优雅退出。

## Event set (v1)

- `service_ready`
- `status`
- `pong`
- `error`
- `service_exiting`

### Reserved vNext events

- `interim`
- `final_raw`
- `final_resolved`
- `fallback_required`

## Timeout budget (v1 defaults)

- `tip_connect_timeout_ms = 250`
- `edit_session_timeout_ms = 200`
- `service_response_timeout_ms = 1500`
- `fallback_activation_timeout_ms = 300`

## Timeout owner

### TIP side

负责：

- active context 绑定等待
- edit session 获取等待
- composition cleanup 超时

### Service side

负责：

- service 内部命令处理超时
- ASR session 内部响应等待

### Coordinator / bridge side

负责：

- TIP 不可用后触发 fallback 的总预算
- 记录 `fallback_reason`

## Error codes / fallback reasons

建议 reason code：

- `tip_connect_failed`
- `tip_connect_timeout`
- `edit_session_failed`
- `edit_session_timeout`
- `service_unavailable`
- `service_timeout`
- `context_invalidated`
- `composition_cleanup_failed`

## Single-writer rule

同一 `session_id` 只能存在一个 final writer：

- TIP 成功提交 `resolved final` 时，legacy 注入不得再写
- 若触发 fallback，则 TIP 必须先 cleanup/cancel，再由 legacy writer 接管

## Logging minimum fields

- `session_id`
- `writer_owner`
- `fallback_reason`
- `composition_cleanup`
- `tip_context_id`
