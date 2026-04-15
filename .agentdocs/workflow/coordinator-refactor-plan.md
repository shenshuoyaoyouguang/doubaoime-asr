# coordinator.py 渐进式拆分实施计划

## 概述

目标：将 `coordinator.py` (1491行) 按职责拆分为 7 个独立模块，降低单文件复杂度，便于维护和扩展。

## 拆分后的文件结构

```
doubaoime_asr/agent/
├── coordinator.py              # 主协调器（精简后预计 ~500行）
├── coordinator_tray.py         # 托盘 UI 相关
├── coordinator_cli.py          # CLI 入口函数
├── coordinator_worker_events.py # Worker 事件处理
├── coordinator_transcript_flow.py # Transcript/interim 流程
├── coordinator_finalization.py # 最终结果提交与注入
├── coordinator_config_runtime.py # 配置应用与回滚
└── coordinator_privileges.py   # 权限与前景监控
```

## 实施顺序

### 阶段 1：创建空模块骨架（第1-2步）

**Step 1: 创建 `coordinator_tray.py`**
```python
# 临时空模块，后续迁移
class TrayController:
    pass
```

**Step 2: 创建 `coordinator_cli.py`**
```python
# CLI 入口函数
def build_arg_parser(): ...
def build_config_from_args(): ...
def normalize_cli_hotkey(): ...
```

### 阶段 2：迁移独立功能（第3-5步）

**Step 3: 迁移 CLI 入口 → `coordinator_cli.py`**

需要迁移的函数：
- `build_arg_parser` (约 40 行)
- `build_config_from_args` (约 30 行)
- `normalize_cli_hotkey` (约 15 行)

**Step 4: 迁移托盘逻辑 → `coordinator_tray.py`**

需要迁移的内容：
- `_start_tray` 方法（约 60 行）
- 5 个局部函数：`build_icon`, `open_log_dir`, `open_settings`, `stop_app`, `restart_app_as_admin`

**Step 5: 迁移配置应用 → `coordinator_config_runtime.py`**

需要迁移的方法：
- `_apply_config` (约 88 行)
- `_polisher_config_changed` (约 15 行)
- `_preview_settings_overlay` (约 20 行)
- `_run_preview_overlay` (约 15 行)

### 阶段 3：核心逻辑拆分（第6-8步）

**Step 6: 迁移 Transcript 流程 → `coordinator_transcript_flow.py`**

需要迁移的方法：
- `_record_session_text`
- `_resolve_segment_index`
- `_next_segment_index`
- `_aggregate_session_text`
- `_submit_interim_snapshot`
- `_flush_interim_snapshot`
- `_flush_interim_dispatcher`
- `_close_interim_dispatcher`
- `_ensure_interim_dispatcher`
- `_concat_transcript_text`
- `_current_target_profile`
- `_text_digest`

以及相关的 Transcript 兼容属性代理：
- `_segment_texts` getter/setter
- `_finalized_segment_indexes` getter/setter
- `_active_segment_index` getter/setter
- `_last_displayed_raw_final_text` getter/setter

**Step 7: 迁移最终结果提交 → `coordinator_finalization.py`**

需要迁移的方法：
- `_inject_final` (约 40 行)
- `_resolve_final_text` (约 25 行)
- `_resolve_committed_text` (约 20 行)
- `_status_for_final_result` (约 25 行)
- `_status_for_error` (约 8 行)

**Step 8: 迁移权限监控 → `coordinator_privileges.py`**

需要迁移的方法：
- `_record_elevation_warning`
- `_clear_elevation_warning`
- `_elevation_status_message`
- `_handle_restart_as_admin`
- `_watch_foreground_target`
- `_check_foreground_elevation`

### 阶段 4：核心事件处理拆分（第9步）

**Step 9: 迁移 Worker 事件处理 → `coordinator_worker_events.py`**

这是最复杂的拆分，需要：
- 将 `_handle_worker_event` 拆分为多个私有方法
- 每个事件类型对应一个处理方法
- 保持与 transcript、finalization、privileges 模块的调用关系

拆分后的方法结构：
```python
async def _handle_worker_event(self, event: WorkerEvent, session: WorkerSession) -> None:
    # 简单的路由分发，不含具体逻辑
    if isinstance(event, WorkerReadyEvent):
        await self._handle_worker_ready(event, session)
    elif isinstance(event, ReadyEvent):
        await self._handle_ready(event, session)
    # ... 其他事件类型

async def _handle_worker_ready(self, event, session): ...
async def _handle_ready(self, event, session): ...
async def _handle_streaming_started(self, event, session): ...
async def _handle_audio_level(self, event, session): ...
async def _handle_interim_result(self, event, session): ...
async def _handle_final_result(self, event, session): ...
async def _handle_error(self, event, session): ...
async def _handle_finished(self, event, session): ...
async def _handle_service_resolved_final(self, event, session): ...
async def _handle_fallback_required(self, event, session): ...
```

### 阶段 5：精简主协调器（第10步）

**Step 10: 精简 `coordinator.py`**

完成上述迁移后，主协调器应该只保留：
- `__init__`（依赖装配）
- 状态与事件队列基础设施（`set_status`, `get_status`, `_emit`, `_emit_threadsafe`）
- 主运行循环（`run`, `_handle_event`）
- 热键处理（`_handle_press`, `_handle_release`, `_send_stop`）
- 会话清理（`_handle_worker_exit`, `_clear_active_session`）
- TIP 相关（`_cancel_tip_session`, `_activate_tip_fallback`）
- 辅助工具方法

预计行数：~500 行

## 模块间依赖关系

```
coordinator.py (主)
  ├── coordinator_cli.py (单向依赖)
  ├── coordinator_tray.py (单向依赖)
  ├── coordinator_worker_events.py (双向)
  │   ├── coordinator_transcript_flow.py (依赖)
  │   ├── coordinator_finalization.py (依赖)
  │   └── coordinator_config_runtime.py (依赖)
  ├── coordinator_config_runtime.py (单向依赖)
  ├── coordinator_privileges.py (单向依赖)
  └── coordinator_transcript_flow.py (部分依赖)
```

## 迁移技术要点

### 1. 保持向后兼容

迁移初期，新模块的方法通过 `from .coordinator_xxx import ...` 导入到原模块再导出：

```python
# coordinator.py 迁移期间
from .coordinator_tray import _start_tray as _start_tray

# 后续完全移除时更新导入方
```

### 2. 处理循环依赖

如果出现循环依赖（如 worker_events 需要 transcript_flow 的方法），解决方案：
- 将共享的工具函数提取到新模块 `coordinator_utils.py`
- 或者通过主协调器中转调用

### 3. 保持测试覆盖

每迁移一个方法后：
- 运行现有测试确保没有回归
- 不需要立刻为迁移的代码编写新测试（原测试仍然覆盖）

## 风险控制

| 风险 | 缓解措施 |
|------|----------|
| 迁移时遗漏方法 | 按上述顺序逐步迁移，每步验证 |
| 破坏现有调用方 | 新模块先作为原模块的 wrapper，逐步切换 |
| 引入循环依赖 | 提前规划依赖关系，避免紧密耦合 |
| 运行时错误 | 每阶段迁移后进行功能验证 |

## 预计工作量

| 阶段 | 步骤 | 预估行数变更 |
|------|------|-------------|
| 1 | 创建空模块骨架 | +50 行 |
| 2 | CLI + Tray + Config | -150 行 |
| 3 | Transcript + Finalization + Privileges | -300 行 |
| 4 | Worker Events | -350 行 |
| 5 | 精简主协调器 | -150 行 |
| **总计** | | **-900 行** |

## 下一步

1. 确认此实施计划是否可行
2. 决定是否立即开始实施
3. 如果开始，从哪个阶段/步骤优先执行