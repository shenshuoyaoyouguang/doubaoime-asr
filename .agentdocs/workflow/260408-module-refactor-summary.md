# 模块级重构设计与测试证据汇总（阶段版）

> 日期：2026-04-08  
> 状态：阶段性汇总，供最终交付包继续扩展

## 1. 汇总目标

本文件用于按模块汇总：

- 当前已完成的重构收口
- 设计意图
- 已有测试与验证证据

## 2. 模块汇总

### 2.1 ASR 协议与模型层

涉及文件：

- `doubaoime_asr/asr.py`
- `doubaoime_asr/asr_models.py`
- `doubaoime_asr/asr_protocol.py`
- `tests/test_asr_protocol.py`

已完成：

- 将 ASR 主入口中的协议/模型边界拆出
- 收口请求/事件结构定义，降低主入口复杂度

设计意图：

- 让 `asr.py` 更聚焦在 orchestration
- 让协议模型与事件结构可单测

测试证据：

- `tests/test_asr_protocol.py`

### 2.2 settings 纯逻辑层

涉及文件：

- `doubaoime_asr/agent/settings_window.py`
- `doubaoime_asr/agent/settings_schema.py`
- `doubaoime_asr/agent/settings_mapping.py`
- `doubaoime_asr/agent/settings_validation.py`

已完成：

- 将 settings 的 schema / mapping / validation 逻辑拆离 UI 主体

设计意图：

- 让 UI 层减少纯逻辑堆叠
- 提高设置保存与验证逻辑可维护性

测试证据：

- `tests/test_settings_window.py`

### 2.3 transcript / coordinator 纯逻辑层

涉及文件：

- `doubaoime_asr/agent/coordinator.py`
- `doubaoime_asr/agent/transcript_utils.py`
- `tests/test_transcript_utils.py`
- `tests/test_coordinator.py`

已完成：

- transcript 聚合纯逻辑下沉

设计意图：

- 将文本拼接与聚合策略从 coordinator 主体中抽离
- 保持状态机主体更聚焦

测试证据：

- `tests/test_transcript_utils.py`
- `tests/test_coordinator.py`

### 2.4 config update 判定层

涉及文件：

- `doubaoime_asr/agent/config_update_plan.py`
- `doubaoime_asr/agent/stable_simple_app.py`
- `doubaoime_asr/agent/coordinator.py`
- `tests/test_config_update_plan.py`

已完成：

- 配置变更公共判定收口

设计意图：

- 热键 / worker / polisher 的变更判定统一
- 减少多处重复条件分支

测试证据：

- `tests/test_config_update_plan.py`
- `tests/test_stable_simple_app.py`
- `tests/test_coordinator.py`

### 2.5 会话主干 / worker 生命周期

涉及文件：

- `doubaoime_asr/agent/session_manager.py`
- `doubaoime_asr/agent/worker_main.py`
- `doubaoime_asr/agent/stable_simple_app_runtime.py`
- `tests/test_session_manager.py`
- `tests/test_worker_main.py`

已完成：

- worker ready / timeout / terminate / dispose 护栏补强
- subprocess pipe 清理加固

设计意图：

- 明确 worker 生命周期
- 降低 subprocess 泄漏和清理时序问题

测试证据：

- `tests/test_session_manager.py`
- `tests/test_worker_main.py`

### 2.6 stable facade 兼容层

涉及文件：

- `doubaoime_asr/agent/stable_simple_app.py`
- `doubaoime_asr/agent/stable_simple_app_compat.py`
- `doubaoime_asr/agent/stable_simple_app_runtime.py`
- `doubaoime_asr/agent/stable_simple_app_bootstrap.py`
- `doubaoime_asr/agent/stable_simple_app_bridge.py`
- `doubaoime_asr/agent/stable_simple_app_worker_bridge.py`
- `doubaoime_asr/agent/stable_simple_app_coordinator_bridge.py`
- `doubaoime_asr/agent/stable_simple_app_state_bridge.py`
- `doubaoime_asr/agent/stable_simple_app_session_bridge.py`
- `tests/test_stable_simple_app.py`

已完成：

- facade 多轮瘦身
- bootstrap / bridge / worker / coordinator / state / session helper 拆分
- session override 关键语义护栏保留

设计意图：

- 保留 legacy facade / monkeypatch 兼容面
- 将复杂实现迁出 facade 主文件

测试证据：

- `tests/test_stable_simple_app.py`

### 2.7 Overlay / 运行时治理

涉及文件：

- `doubaoime_asr/agent/overlay_service.py`
- `doubaoime_asr/agent/overlay_scheduler.py`
- `tests/test_overlay_service.py`
- `tests/test_overlay_scheduler.py`

已完成：

- overlay runtime component 安装入口统一
- scheduler guard/build 逻辑收口

设计意图：

- 降低 runtime 装配分散度
- 提高 overlay 生命周期一致性

测试证据：

- `tests/test_overlay_service.py`
- `tests/test_overlay_scheduler.py`

## 3. 当前整体验证证据

- 定向回归：`177 passed`
- 全量回归：`450 passed`

## 4. 当前仍需补齐

- 模块级设计文档的更细粒度变更范围说明
- 模块级风险与回滚点清单
- 最终交付版测试证据归档
