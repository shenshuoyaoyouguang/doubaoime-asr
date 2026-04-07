# 项目目标架构设计（阶段版）

> 日期：2026-04-08  
> 状态：阶段性草案，后续持续补齐

## 1. 目标

在**业务无感知**前提下，将系统收敛为：

- facade 更薄
- 边界更清晰
- 运行时职责集中
- 配置更新与回滚可预测
- 测试与交付证据可持续积累

## 2. 目标分层

### 2.1 入口/兼容层
- `stable_simple_app.py`

目标：
- 仅保留向后兼容 facade
- 保留旧测试/monkeypatch 入口
- 不直接承载复杂运行时逻辑

### 2.2 兼容桥接层
- `stable_simple_app_bridge.py`
- `stable_simple_app_worker_bridge.py`
- `stable_simple_app_coordinator_bridge.py`
- `stable_simple_app_state_bridge.py`
- `stable_simple_app_session_bridge.py`
- `stable_simple_app_bootstrap.py`

目标：
- 按边界组织 helper
- 将 facade 中的纯转发、状态桥接、session 包装、worker 边界、bootstrap 逻辑分离

### 2.3 运行时业务层
- `coordinator.py`
- `session_manager.py`
- `overlay_service.py`
- `injection_manager.py`
- `text_polisher.py`

目标：
- 让业务状态机与运行时行为集中在 coordinator/session_manager 侧
- helper 仅桥接，不承载核心业务决策

### 2.4 平台适配层
- Windows 热键
- 权限提升
- overlay native 组件
- 注入器与 terminal 特化路径

目标：
- 将平台差异继续隔离在边界模块中
- 避免向上层传播平台细节

## 3. 目标架构原则

### 3.1 facade 最小化
- facade 只做兼容
- helper 负责纯桥接
- coordinator/session_manager 负责状态机

### 3.2 高内聚边界
- session 相关逻辑归 session helper / session_manager
- worker 相关逻辑归 worker bridge / session_manager
- 状态与属性代理归 state bridge
- 运行时初始化归 bootstrap

### 3.3 回滚与兼容优先
- 配置变更必须支持回滚
- worker/listener/polisher 变更必须支持延迟生效与失败恢复
- 旧接口保持稳定

### 3.4 测试护栏优先
- 每一轮结构收口必须先有定向回归
- 最终必须通过全量回归

## 4. 当前离目标架构还差什么

### 4.1 代码侧
- `stable_simple_app` 仍残留少量 compat 边界方法
- runtime/compat 双轨仍需继续谨慎收尾

### 4.2 文档侧
- 原生架构图谱尚未整理成正式交付版
- 目标架构图谱尚未整理成正式交付版
- 模块级设计说明尚未统一汇编

### 4.3 交付侧
- 需要形成标准化：
  - 回归测试报告
  - 架构合规校验说明
  - 部署与回滚预案

## 5. 下一阶段建议

1. 完成 `stable_simple_app` 剩余少量兼容边界清点  
2. 开始整理：
   - 原生架构文档
   - 目标架构文档
   - 模块级设计与测试证据
3. 逐步转入最终交付物组装阶段
