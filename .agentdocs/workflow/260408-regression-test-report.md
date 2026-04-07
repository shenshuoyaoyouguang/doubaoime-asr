# 全量回归测试报告（阶段版）

> 日期：2026-04-08  
> 状态：阶段性回归报告，随重构推进持续更新

## 1. 报告目的

本报告用于记录当前重构阶段的验证结果，证明在已完成的结构收口范围内：

- 核心业务行为未发生有意变更
- 主要兼容面仍保持可用
- 当前代码可持续进入下一轮重构或交付整理

## 2. 当前验证结论

### 2.1 定向回归

执行命令：

```powershell
pytest tests/test_overlay_service.py tests/test_overlay_scheduler.py tests/test_stable_simple_app.py tests/test_coordinator.py tests/test_session_manager.py tests/test_worker_main.py -q
```

结果：

- **177 passed**

覆盖重点：

- `stable_simple_app` facade/compat/helper 兼容面
- coordinator 会话主干
- session manager worker 生命周期
- worker 主进程关键护栏
- overlay service / scheduler 行为

### 2.2 全量回归

执行命令：

```powershell
pytest -q
```

结果：

- **450 passed**

## 3. 本阶段重点验证内容

### 3.1 会话主干
- worker ready / streaming / finished / exit 事件处理
- 配置更新后的 listener / worker / polisher 延迟生效
- worker timeout / terminate / dispose 行为

### 3.2 facade 兼容面
- `stable_simple_app` legacy facade 保持可调用
- monkeypatch 落点保持稳定
- compat / runtime / session override 关键边界未破坏

### 3.3 Overlay 与运行时治理
- overlay runtime component 安装入口统一后行为保持不变
- overlay scheduler 与 service 在未启动/已启动时的行为一致

### 3.4 状态与桥接
- state/property facade 代理仍正确映射到 coordinator
- status/result bridge 行为与旧路径一致
- control/session/worker bridge 保持原有语义

## 4. 与基线对比

当前阶段相较最早基线：

- 初始基线：`408 passed, 1 warning`
- 当前阶段：`450 passed`

说明：

- 回归通过数增加主要来自新增测试护栏
- 当前重构阶段已持续保持全量通过

## 5. 尚未覆盖为正式交付报告的内容

以下内容仍建议在最终交付前补齐为正式版：

1. 按模块拆分的测试证据汇总
2. 冒烟测试步骤与截图/日志摘录
3. 构建产物验证记录
4. 部署前检查清单执行记录

## 6. 当前判断

基于本阶段回归结果，蕾姆判断：

- 当前已完成重构部分可继续进入交付物整理阶段
- 后续若继续做小步收口，仍应维持：
  - 先定向回归
  - 再全量回归
