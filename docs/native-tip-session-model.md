# Native TIP Session Model (v1)

## Goal

定义 v1 的：

- active TIP rendezvous
- hotkey bridge
- commit point
- single-writer ownership
- focus drift / invalidation
- fallback cleanup

## Active TIP rendezvous

v1 采用：

- **全局热键触发**
- 由当前前景宿主中的活动 TIP 实例接管本次 session
- 实验性 rendezvous 骨架阶段增加：
  - `register_active_context`
  - `query_active_context`
  - `clear_active_context`
    用于 bridge/controller 在发起 `begin_session` 前确认当前 native host 暴露的 active context
  - `edit_session_ready`
    用于标记当前 active context 是否已具备进入 edit session 的条件；未 ready 时不得开始主链路 session

意味着：

1. 全局热键被现有运行时捕获
2. bridge 层解析当前活动上下文
3. 只把命令投递给当前活动 TIP 实例

## Trigger owner

v1：**global hotkey bridge**

非 v1：

- preserved key
- 语言栏高级切换

## Commit point

v1 明确定义：

- interim：只更新 composition
- final commit：只提交 **resolved final**
- raw final 不直接写入宿主

## Single-writer rule

每个 `session_id` 只能有一个最终写入 owner：

- 若 TIP 主路径成功：owner = `tip`
- 若 TIP 明确失败/超时并切换 fallback：owner = `legacy`

不允许：

- TIP commit 后 legacy 再注入
- legacy 注入后 TIP 再 commit

## Focus drift / context invalidation

以下情况视为 context invalidated：

- 前景窗口变化
- selection 失效
- edit session 无法继续

v1 策略：

1. 先尝试 cancel composition
2. 若 cleanup 成功，再决定是否 fallback
3. 若 cleanup 失败，必须记录 `composition_cleanup_failed`

## Fallback cleanup

fallback 前必须保证：

1. 已取消或清理现有 composition
2. 已释放 TIP final writer ownership
3. legacy 路径接管前记录 `fallback_reason`

## Minimum observability

每次 session 最少记录：

- `session_id`
- `tip_context_id`
- `writer_owner`
- `fallback_reason`
- `composition_cleanup`
