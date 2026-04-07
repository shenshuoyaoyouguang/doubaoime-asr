from __future__ import annotations

import asyncio
import contextlib
from typing import Any

from .config import AgentConfig
from .config_update_plan import build_config_update_plan
from .composition import CompositionSession
from .input_injector import FocusChangedError
from .input_injector import FocusTarget


def clear_session_state(app: Any) -> None:
    """清理 compat 路径的会话状态。"""
    app._coordinator.session_manager.clear_session()
    app._coordinator.injection_service.end_session()


def handle_inline_focus_changed(app: Any, log_tag: str) -> None:
    """处理 compat 路径的流式焦点变化。"""
    session = app._session
    if session is None:
        return
    if hasattr(session, "inline_streaming_enabled"):
        session.inline_streaming_enabled = False
    if hasattr(session, "final_injection_blocked"):
        session.final_injection_blocked = True
    if hasattr(session, "target"):
        session.target = None


def handle_inline_failure(
    app: Any,
    composition: Any,
    *,
    log_tag: str,
    fallback_status: str | None = None,
    blocked_status: str = "实时上屏失败，仅保留识别",
) -> bool:
    """处理 compat 路径的流式失败。"""
    composed_text_exists = bool(
        composition is not None
        and (
            getattr(composition, "rendered_text", "")
            or getattr(composition, "final_text", "")
        )
    )
    if composed_text_exists:
        handle_inline_focus_changed(app, log_tag)
        app.set_status(blocked_status)
    elif fallback_status:
        app.set_status(fallback_status)
    app.logger.exception(log_tag)
    return composed_text_exists


async def handle_press(app: Any) -> None:
    """处理 compat 路径的热键按下。"""
    app.logger.info("hotkey_down")
    session = await app._ensure_worker()
    use_runtime_session_flow = app._uses_runtime_session_flow(session)

    from .session_manager import WorkerSessionState

    real_state = getattr(getattr(session, "_real", session), "state", None)
    if real_state == WorkerSessionState.STREAMING:
        return

    target: FocusTarget | None = None
    inline_streaming_enabled = False
    composition = None

    if app.mode == "inject":
        target = app.injection_manager.capture_target()
        if target is None:
            app.set_status("未检测到可写入焦点")
            return
        if app._target_requires_admin(target):
            app._record_elevation_warning(target, log_tag="press_blocked_elevated_target")
            return
        app._clear_elevation_warning()
        app.logger.info(
            "captured_target hwnd=%s focus_hwnd=%s process=%s terminal=%s elevated=%s",
            target.hwnd,
            getattr(target, "focus_hwnd", None),
            target.process_name,
            getattr(target, "terminal_kind", None),
            target.is_elevated,
        )
        if app._should_enable_inline_streaming(target):
            inline_streaming_enabled = True
            if not use_runtime_session_flow:
                composition = CompositionSession(app.injection_manager.injector, target)
    else:
        app.logger.info("inject_skipped reason=recognize_mode phase=session_start")

    if not use_runtime_session_flow:
        if hasattr(session, "begin"):
            session.begin(
                target,
                app.mode,
                composition=composition,
                inline_streaming_enabled=inline_streaming_enabled,
            )
    else:
        app._coordinator.session_manager.begin_session(target, app.mode)
        app._coordinator.injection_service.begin_session(
            target,
            app.mode,
            inline_streaming_enabled=inline_streaming_enabled,
        )

    capture_output_warning = app._activate_capture_output()

    try:
        await app._send_worker_command("START")
    except Exception:
        if hasattr(session, "clear_active"):
            session.clear_active()
        clear_session_state(app)
        restore_warning = app._release_capture_output()
        app.logger.exception("worker_start_command_failed")
        app.set_status(restore_warning or "启动识别失败，请查看 controller.log")
        await app._restart_worker()
        return

    await app.overlay_scheduler.show_microphone("正在聆听…")
    app.set_status(app._session_start_status(capture_output_warning))
    app._coordinator._segment_texts.clear()
    app._coordinator._finalized_segment_indexes.clear()
    app._coordinator._active_segment_index = None
    app._coordinator._last_displayed_raw_final_text = ""


async def send_stop(app: Any, log_tag: str, status: str) -> None:
    """发送 compat 路径 STOP。"""
    session = app._session
    if session is None:
        return
    await app._send_worker_command("STOP")
    if hasattr(session, "stop_sent"):
        session.stop_sent = True
    if hasattr(session, "pending_stop"):
        session.pending_stop = False
    await app.overlay_scheduler.stop_microphone()
    app.logger.info(log_tag)
    app.set_status(status)


async def inject_final(app: Any, text: str) -> None:
    """处理 compat 路径最终注入。"""
    if not app._has_test_session_override():
        await app._runtime_inject_final_impl(text)
        return
    if not text:
        return
    session = app._session
    if session is None:
        return
    if getattr(session, "mode", "inject") != "inject":
        app.logger.info("inject_skipped reason=recognize_mode text_length=%s", len(text))
        return
    if getattr(session, "final_injection_blocked", False):
        return

    target = getattr(session, "target", None)
    composition = getattr(session, "composition", None)
    inline_streaming_enabled = getattr(session, "inline_streaming_enabled", False)

    if composition is not None and inline_streaming_enabled:
        try:
            if (
                getattr(composition, "rendered_text", None) != text
                or getattr(composition, "final_text", None) != text
            ):
                composition.finalize(text)
            app.logger.info("inject_success method=inline_composition")
        except FocusChangedError:
            handle_inline_focus_changed(app, "inject_final")
            app.logger.warning("inject_focus_changed")
        except Exception:
            handle_inline_failure(app, composition, log_tag="inject_inline_final_failed")
        return

    if target is None:
        return
    if app._target_requires_admin(target):
        return
    try:
        result = await app.injection_manager.inject_text(target, text)
        if result:
            app.logger.info(
                "inject_success method=%s", getattr(result, "method", "unknown")
            )
    except FocusChangedError:
        handle_inline_focus_changed(app, "inject_final_focus_changed")
        app.logger.warning("inject_focus_changed")
        app.set_status("焦点已变化，仅保留识别")
    except Exception:
        app.logger.exception("inject_final_failed")
        app.set_status("注入失败，仅保留识别")


async def clear_active_session(app: Any) -> None:
    """清除 compat 路径活跃会话。"""
    if not app._has_test_session_override():
        await app._runtime_clear_active_session_impl()
        return
    session = app._session
    if session is not None and hasattr(session, "clear_active"):
        session.clear_active()
    app._coordinator.injection_service.end_session()
    restore_warning = app._release_capture_output()
    if restore_warning is not None:
        app.set_status(restore_warning)
    app._apply_pending_listener_rebind("listener_rebind_failed_after_session")
    if app._pending_worker_restart:
        app._pending_worker_restart = False
        await app._restart_worker()
    if app._pending_polisher_warmup:
        app._pending_polisher_warmup = False
        app._schedule_polisher_warmup("after_session")


async def send_stop_if_needed(app: Any) -> None:
    """在 compat 路径按需发送 STOP。"""
    if not app._has_test_session_override():
        await app._runtime_send_stop_if_needed_impl()
        return
    session = app._session
    if (
        session is None
        or getattr(session, "stop_sent", False)
        or not getattr(session, "pending_stop", False)
    ):
        return
    await send_stop(app, "worker_stop_sent_after_ready", "等待最终结果…")


async def apply_inline_interim(app: Any, text: str) -> None:
    """处理 compat 路径流式 interim。"""
    if not app._has_test_session_override():
        await app._runtime_apply_inline_interim_impl(text)
        return
    session = app._session
    if session is None:
        return
    if not getattr(session, "inline_streaming_enabled", False):
        return
    composition = getattr(session, "composition", None)
    if composition is None:
        return
    if getattr(composition, "rendered_text", None) == text:
        return
    try:
        composition.render_interim(text)
    except FocusChangedError:
        handle_inline_focus_changed(app, "apply_inline_interim")
        app.logger.warning("inline_streaming_focus_changed")
    except Exception:
        handle_inline_failure(app, composition, log_tag="inline_streaming_failed")


async def prepare_final_text_compat(app: Any, text: str) -> None:
    """准备 compat 路径的最终文本。"""
    if not app._has_test_session_override():
        await app._runtime_prepare_final_text_impl(text)
        return
    session = app._session
    if session is None:
        return
    if not getattr(session, "inline_streaming_enabled", False):
        return
    composition = getattr(session, "composition", None)
    if composition is None:
        return
    if (
        getattr(composition, "rendered_text", None) == text
        and getattr(composition, "final_text", None) == text
    ):
        return
    try:
        composition.finalize(text)
    except FocusChangedError:
        handle_inline_focus_changed(app, "prepare_final_text")
        app.logger.warning("inline_final_focus_changed")
    except Exception:
        handle_inline_failure(app, composition, log_tag="inline_final_prepare_failed")


async def terminate_worker(app: Any) -> None:
    """终止 compat 路径 worker。"""
    session = app._session
    if session is None:
        return

    process = getattr(session, "process", None)
    if process is None:
        return

    grace_timeout_s = app.config.worker_exit_grace_timeout_seconds()
    kill_wait_timeout_s = app.config.worker_kill_wait_timeout_seconds()

    if (
        getattr(process, "stdin", None) is not None
        and getattr(process, "returncode", 1) is None
    ):
        try:
            await app._send_worker_command("EXIT")
        except Exception:
            pass

    try:
        await asyncio.wait_for(process.wait(), timeout=grace_timeout_s)
    except (asyncio.TimeoutError, ProcessLookupError):
        try:
            process.kill()
        except ProcessLookupError:
            pass
        try:
            await asyncio.wait_for(process.wait(), timeout=kill_wait_timeout_s)
        except (asyncio.TimeoutError, ProcessLookupError):
            pass

    app._coordinator.session_manager._session = None
    app._reset_test_session_override()


async def ensure_worker(app: Any) -> Any:
    """确保 compat 路径 worker 已就绪。"""
    if app._has_test_session_override():
        return app._session

    sm = app._coordinator.session_manager

    if sm._session is not None and sm._session.process.returncode is None:
        if sm._session.process_ready:
            return app._wrap_session(sm._session)

    if sm._session is not None and sm._session.process.returncode is not None:
        await app._dispose_worker()

    process = await app._spawn_worker()
    sm._next_session_id += 1
    from .session_manager import WorkerSession as _RealSession, WorkerSessionState

    session = _RealSession(
        session_id=sm._next_session_id,
        process=process,
        state=WorkerSessionState.STARTING,
    )
    session.stdout_task = asyncio.create_task(
        app._read_worker_stdout(process.stdout, app._wrap_session(session))
    )
    session.stderr_task = asyncio.create_task(app._read_worker_stderr(process.stderr))
    session.wait_task = asyncio.create_task(
        app._wait_worker(process, session.session_id)
    )
    sm._session = session

    loop = asyncio.get_running_loop()
    timeout_s = app._select_worker_ready_timeout_seconds()
    deadline = loop.time() + timeout_s
    while loop.time() < deadline:
        if session.process_ready:
            session.transition_to(WorkerSessionState.READY)
            sm._worker_started_once = True
            return app._wrap_session(session)
        if session.process.returncode is not None:
            break
        await asyncio.sleep(0.02)

    await app._terminate_session_process(app._wrap_session(session))
    await app._dispose_worker()
    raise RuntimeError("worker process did not become ready")


def _matching_session(app: Any, session_id: int) -> tuple[Any | None, int | None]:
    session = app._session
    current_session_id = getattr(session, "session_id", None) if session is not None else None
    if session is None or current_session_id != session_id:
        return None, current_session_id
    return session, current_session_id


async def handle_worker_exit(app: Any, session_id: int, code: int) -> None:
    """处理 compat facade 的 worker 退出。"""
    session, current_session_id = _matching_session(app, session_id)
    if session is None:
        app.logger.info(
            "worker_exit_ignored session_id=%s current_session_id=%s code=%s",
            session_id,
            current_session_id,
            code,
        )
        return

    app.logger.info("worker_exit code=%s", code)
    if not app._stopping and code != 0 and not app._status.startswith("识别失败"):
        app.set_status(f"识别进程异常退出: {code}")

    restore_warning = app._release_capture_output()
    if restore_warning is not None:
        app.set_status(restore_warning)

    # Worker 已退出，只回收资源，避免再次 wait。
    await app._dispose_worker()
    app._reset_test_session_override()

    if not app._stopping:
        app._apply_pending_listener_rebind("listener_rebind_failed_after_worker_exit")

    if not app._stopping and app._pending_worker_restart:
        app._pending_worker_restart = False
        with contextlib.suppress(Exception):
            await app._coordinator.session_manager.ensure_worker()


async def handle_worker_event(app: Any, session_id: int, event: object) -> None:
    """处理 compat facade 的 worker 事件并转交 coordinator。"""
    session, current_session_id = _matching_session(app, session_id)
    if session is None:
        event_type = event.get("type") if isinstance(event, dict) else type(event).__name__
        app.logger.info(
            "worker_event_ignored session_id=%s current_session_id=%s type=%s",
            session_id,
            current_session_id,
            event_type,
        )
        return
    if not isinstance(event, dict):
        return

    from .events import parse_worker_event

    await app._coordinator._handle_worker_event(parse_worker_event(event))


def configure_services(app: Any, config: AgentConfig) -> None:
    """统一更新 compat facade 下游服务配置。"""
    app._coordinator.session_manager.config = config
    app.preview.configure(config)
    app._coordinator.injection_service.configure(config)
    app._coordinator.text_polisher.configure(config)
    app._coordinator.capture_output_guard.configure(config.capture_output_policy)


async def apply_config_update(app: Any, new_config: AgentConfig) -> None:
    """处理 compat facade 的配置更新与回滚。"""
    old_config = app.config
    old_mode = app.mode
    old_pending_listener_rebind = app._pending_listener_rebind
    old_pending_worker_restart = app._pending_worker_restart
    old_pending_polisher_warmup = app._pending_polisher_warmup

    update_plan = build_config_update_plan(old_config, new_config)
    session_active = app._coordinator.session_manager.is_streaming()
    listener_rebound = False
    worker_restarted = False

    try:
        app.config = new_config
        app.mode = new_config.mode
        configure_services(app, new_config)

        if update_plan.hotkey_changed:
            if session_active:
                app._pending_listener_rebind = True
            else:
                app._rebind_listener(new_config.effective_hotkey_vk())
                listener_rebound = True

        if update_plan.worker_changed:
            if session_active:
                app._pending_worker_restart = True
            else:
                await app._restart_worker()
                worker_restarted = True

        if update_plan.polisher_changed:
            if session_active:
                app._pending_polisher_warmup = True
            else:
                app._schedule_polisher_warmup("config_update")

        app.config.save()
    except Exception:
        app.logger.exception("apply_config_failed")
        app.config = old_config
        app.mode = old_mode
        app._pending_listener_rebind = old_pending_listener_rebind
        app._pending_worker_restart = old_pending_worker_restart
        app._pending_polisher_warmup = old_pending_polisher_warmup
        configure_services(app, old_config)
        if listener_rebound:
            app._rebind_listener(old_config.effective_hotkey_vk())
        if worker_restarted:
            await app._restart_worker()
        with contextlib.suppress(Exception):
            app.config.save()
        app.set_status("设置保存失败，已恢复旧配置")
        return

    if not session_active:
        if update_plan.hotkey_changed:
            app.set_status(f"热键已更新为 {new_config.effective_hotkey_display()}")
        elif update_plan.worker_changed:
            app.set_status("设置已保存并重启识别服务")
        elif update_plan.polisher_changed:
            app.set_status("设置已保存并更新润色配置")
        else:
            app.set_status("设置已保存")
    else:
        app.logger.info("settings_saved_during_active_session")
