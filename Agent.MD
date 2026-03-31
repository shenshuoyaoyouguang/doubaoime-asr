# Agent Notes

- `doubaoime_asr/agent/stable_simple_app.py` 的 compat 包装层不能直接覆盖运行态实现。
  - `SessionManager._on_event` 必须桥接回 coordinator 事件队列，否则 `run()` 路径会丢 worker interim/final/finished/exit 事件。
  - `InjectionService.begin_session()` 创建的运行态 inline composition 状态不在 compat `app._session` 上；inline/最终提交逻辑必须保留 runtime 原始实现，只有显式测试会话覆盖时才走 compat 包装。
  - 覆盖协调器方法时要先保存原始实现；像 `_send_stop_if_needed()` 这类互相委托的方法如果双向重定向，容易产生递归。
  - wrapper 级 `is_current_process_elevated` / `restart_as_admin` monkeypatch 需要在 coordinator 构造后立即同步到 coordinator/injection_service，不能只在 coordinator 构造函数里计算一次。
  - 旧 `StableVoiceInputApp._handle_press()` API 仍可能被直接调用；当 `_ensure_worker()` 返回运行态 `_SessionCompatWrapper` 时，必须走 `session_manager.begin_session()` + `injection_service.begin_session()`，不能继续把 `composition=` / `inline_streaming_enabled=` 转发给运行态 `session_manager.WorkerSession.begin()`。
  - 旧 `StableVoiceInputApp._handle_worker_event(session_id, event)` API 仍需要保留 `session_id` 守卫；过时 worker 的 delayed finished/error 事件必须被丢弃，不能直接转发给 coordinator 覆盖当前活跃会话。
- `doubaoime_asr/agent/coordinator.py` 本体也必须自带运行态连线：
  - 直接实例化 `VoiceInputCoordinator` 时，`SessionManager` 需要在构造时就绑定 `on_event`，否则 worker stdout 解析出的 ready/interim/final/finished 事件不会进入协调器事件队列。
  - `VoiceInputCoordinator` 在启动时计算出的 `_process_elevated` 需要同步写入 `InjectionService.set_process_elevated()`；否则管理员目标仍会被 `target_requires_admin()` 误判为不可注入。
