# 项目重构全局基线（阶段版）

> 日期：2026-04-08  
> 状态：阶段性整理，持续更新中

## 1. 项目目标

`doubaoime-asr` 当前包含两条主线：

1. **ASR 核心库**
   - 负责设备注册、鉴权、音频编码、协议通信、结果解析
2. **Windows 桌面代理**
   - 负责热键、录音会话、worker 生命周期、浮层显示、文本注入、托盘与权限交互

## 2. 当前架构基线

### 2.1 ASR 核心层
- `doubaoime_asr/asr.py`
- `doubaoime_asr/asr_models.py`
- `doubaoime_asr/asr_protocol.py`
- `doubaoime_asr/audio.py`
- `doubaoime_asr/device.py`
- `doubaoime_asr/wave_client.py`

职责：
- 请求拼装
- 实时流式识别入口
- 协议模型与事件解析
- 设备注册与 token 获取
- 音频编码与波形传输

### 2.2 Agent 协调层
- `doubaoime_asr/agent/coordinator.py`
- `doubaoime_asr/agent/session_manager.py`
- `doubaoime_asr/agent/worker_main.py`

职责：
- 统一业务状态机
- worker 生命周期管理
- 文本聚合 / interim / final / error / finished 事件处理
- 跨组件状态同步

### 2.3 Stable facade 兼容层
- `doubaoime_asr/agent/stable_simple_app.py`

当前定位：
- 保留旧 facade / monkeypatch 兼容面
- 作为兼容入口，不再承载大块运行时实现

已拆出的 helper：
- `stable_simple_app_runtime.py`
- `stable_simple_app_bootstrap.py`
- `stable_simple_app_bridge.py`
- `stable_simple_app_worker_bridge.py`
- `stable_simple_app_coordinator_bridge.py`
- `stable_simple_app_state_bridge.py`
- `stable_simple_app_session_bridge.py`
- `stable_simple_app_compat.py`

### 2.4 文本注入与会话层
- `injection_manager.py`
- `input_injector.py`
- `composition.py`

职责：
- 焦点目标捕获
- 注入策略选择
- inline composition / final commit

### 2.5 Overlay 层
- `overlay_service.py`
- `overlay_scheduler.py`
- `overlay_preview.py`
- `overlay_preview_cpp.py`
- `overlay_ui/*`

职责：
- 录音占位、interim、final 文本显示
- scheduler 节流
- native / python fallback

### 2.6 配置与设置层
- `agent/config.py`
- `settings_window.py`
- `settings_schema.py`
- `settings_mapping.py`
- `settings_validation.py`
- `config_update_plan.py`

职责：
- 配置模型
- UI 配置编辑
- 运行时配置更新判定与回滚

## 3. 当前已完成的主要重构收口

### 3.1 低冲突内部分层
- ASR 模型/协议分层
- settings 纯逻辑拆分
- transcript 纯逻辑下沉

### 3.2 会话主干收敛
- config 更新判定收口
- `stable_simple_app` 多轮瘦身
- worker/session 相关兼容边界分离

### 3.3 Overlay / 运行时治理
- overlay runtime component 安装入口统一
- stable facade 的 bridge / state / worker / coordinator / session helper 拆分

## 4. 当前已知约束

- 业务逻辑必须保持不变
- facade 方法名与 monkeypatch 落点必须保留
- `__test_session + _UNSET` 语义不能破坏
- runtime/compat 双轨尚未完全消失，需要继续谨慎收尾

## 5. 当前验证基线

- 定向回归：`177 passed`
- 全量回归：`450 passed`

## 6. 后续进入最终交付前仍需补齐

- 原生架构全景图整理版
- 目标架构整理版
- 模块级重构设计与测试报告汇总
- 交付清单正式版
