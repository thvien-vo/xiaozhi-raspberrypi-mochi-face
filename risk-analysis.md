# py-xiaozhi Potential Risk Analysis Report

**Scope**: `main.py` + `src/` (orchestration, core, plugins, protocols, audio, mcp, ui glue)  
**Date**: 2026-07-19  
**Nature**: Read-only risk inventory; does not include fix implementations  
**Reference**: Existing repository implementation patterns + remediation in this report; does not depend on the removed Trellis scaffolding

---

## 1. Executive Summary

> **Fix Status (2026-07-19)**: Critical / High and P0-P3 items have all been implemented; see Section 6 and Section 9. Regression suite `tests/test_resilience_fixes.py`; startup time ~1.7s. Independent of Trellis.  
> Standards documented in: `.trellis/spec/backend/error-handling.md`, `asyncio-guidelines.md`.  
> Regression: `tests/test_resilience_fixes.py`.

Architecturally, the project follows a three-tier model (`ServiceContainer` -> core services -> plugins), where PluginManager / EventBus / TaskManager are designed with isolation in mind.

**Summary of Original Issues (Resolved)**:

| Theme | Before Fix | After Fix |
|------|--------|--------|
| **(a) Exception Observability** | Numerous `except` blocks without `exc_info` | Core paths uniformly log stack traces; TaskManager uses `exc_info=exc` |
| **(b) Module Isolation** | Continues idling when dependencies fail; dirty Music/MCP singletons | `failed` marker + dependency skip; container bind/unbind |
| **(c) Cascading / Deadlock** | Per-frame `create_task`; zombie wait | Bounded audio queue; critical plugin latch exit 1 |

---

## 2. Exception Swallowing and Observability

### 2.1 Pattern Statistics (`src/` + `main.py`)

| Pattern | Approx. Count | Description |
|------|--------|------|
| `except Exception` | **275** | Primary pattern in the project; permitted by guidelines, but boundary quality varies |
| `except` + logger **without** `exc_info` | **~182** | Violates error-handling guideline "critical paths must include stack traces" |
| `except ...: pass` (including CancelledError/OSError, etc.) | **~29** | Mostly reasonable cleanup; a few silent swallows |
| `except Exception` followed by `pass` / silent `return` / `continue` | **~15** | Genuinely "swallowed with almost no trace" |
| Bare `except:` | **0** | Complies with prohibited items in guidelines |
| `asyncio.create_task` (including legitimately self-managed) | **~20** | Several places bypass TaskManager (see Section 4) |

Search keywords: `except Exception`, `except ...: pass`, `exc_info`, `asyncio.create_task`, `threading.Thread`, `get_instance`.

### 2.2 Critical / High: Observability Failures

#### H-1 EventBus swallows handler exceptions without stack traces  
- **Location**: `src/core/event_bus.py` `_safe_call` ~L166-178  
- **Symptom**: Any subscriber raising an exception logs only a single line `Handler X execution exception: {e}` **without traceback**; combined with `emit` using `gather(..., return_exceptions=True)`, the caller is completely unaware.  
- **Root Cause**: Isolation design is sound, but logging does not follow Pattern #1 of the guidelines (boundaries should set `exc_info=True`).  
- **Blast Radius**: UI, state machine, protocols, music, and all paths routed through EventBus; during debugging, one only sees "state not updating" without knowing what crashed.  
- **Mitigation Direction**: Change `_safe_call` to `logger.error(..., exc_info=True)`; optionally add failure counters/metrics for critical events (such as `DEVICE_STATE_CHANGED`).

#### H-2 PluginManager lifecycle failures lack stack traces  
- **Location**: `src/plugins/manager.py` `setup_all` / `start_all` / `notify_*` ~L150-203  
- **Symptom**: `logger.error(f"Plugin {name} setup failed: {e}")` lacks `exc_info`; failed plugins remain in the list and continue to be called during subsequent `start` / `notify` phases.  
- **Root Cause**: The isolation boundary catches the exception but neither marks the plugin as failed nor preserves the stack trace.  
- **Blast Radius**: Half-initialized plugins fail again or raise NPE-style AttributeErrors in subsequent hooks; noisy logs and hard-to-trace root causes.  
- **Mitigation Direction**: Mark `_failed=True` on setup failure and remove from notify lists; standardize logging with `exc_info=True`; skip `start` for downstream plugins whose `requires` dependencies failed.

#### H-3 TaskManager task exception logging may lose traceback  
- **Location**: `src/core/task_manager.py` `_on_done` ~L94-99  
```python
exc = t.exception()
if exc:
    logger.error(f"Task {name} ended with exception: {exc}", exc_info=True)
```  
- **Symptom**: Inside a done callback there is no active exception context, so `exc_info=True` **typically does not** attach `exc`'s traceback (should use `exc_info=(type(exc), exc, exc.__traceback__)` or `logger.error(..., exc_info=exc)`).  
- **Blast Radius**: Incomplete "ended with exception" logs for all business tasks spawned via `spawn` (UI start, scheduled commands, etc.).  
- **Mitigation Direction**: Use `exc_info=exc` (Python 3.5+) or an explicit 3-tuple.

#### H-4 Protocol/Container JSON hot paths swallow exceptions without stack traces  
- **Location**:  
  - `src/bootstrap/container.py` `_on_incoming_json` ~L362-377: `logger.error(f"Failed to process JSON message: {e}")`  
  - `src/core/protocol_manager.py` `connect` ~L124-126: connection exception without stack trace  
  - `src/core/resource_pool.py` ~L54-55: cleanup failure without stack trace  
- **Symptom**: Only the string message remains when TTS/state machine processing fails.  
- **Blast Radius**: Voice session state corruption is difficult to reproduce.  
- **Mitigation Direction**: Uniformly add `exc_info=True` at boundaries.

### 2.3 Medium: Silent Swallows (Intentional or Near-Silent)

#### M-1 Nested except with outer `pass` in audio cleanup  
- **Location**: `src/plugins/audio.py` `register_resources` ~L140-154  
- **Symptom**: Outer `except Exception: pass` when importing music player fails, **with no logging**.  
- **Blast Radius**: Music/decoder may leak during shutdown; difficult to detect from logs.  
- **Mitigation Direction**: Log at least `logger.debug(..., exc_info=True)`; follows the guideline "when in doubt, use #2 warning".

#### M-2 `_should_send_microphone_audio` silently returns False  
- **Location**: `src/plugins/audio.py` ~L188-193  
```python
except Exception:
    return False
```  
- **Symptom**: Stops sending upstream audio without logging when state query raises an exception.  
- **Blast Radius**: User experiences "suddenly can't hear me speaking", hard to correlate with a state machine bug.  
- **Mitigation Direction**: Log a warning once + return False.

#### M-3 Other silent/degraded returns (acceptable but worth noting)  
| Location | Behavior | Assessment |
|------|------|------|
| `main.py` L163-164 SIGTRAP | `pass` | Acceptable (signal installation) |
| `src/constants/constants.py` L75-77 | frame duration defaults to 20 | Acceptable |
| `src/activation/service.py` L466-467 | efuse read failure -> unactivated | Acceptable, debug log recommended |
| `src/protocols/websocket_protocol.py` L304-305 | `is_audio_channel_opened` exception -> False | Acceptable |
| `src/plugins/manager.py` L54-55 | `get_plugin` exception -> None | Overly defensive, `dict.get` almost never raises |
| MCP app launcher/scanner (multiple places) | Probe failure continue/return | Acceptable on tool side |

### 2.4 Guideline Compliance (error-handling.md)

| Guideline Item | Current Status |
|--------|------|
| Forbid bare `except:` | ✅ None found |
| `exc_info=True` on critical paths | ❌ Widely violated (EventBus, PluginManager, protocols, UI bridge) |
| Forbid `logger.exception` | ✅ Not used |
| Isolate plugin failures in manager; avoid re-swallowing inside plugins | ⚠️ Isolated by manager, though inner try blocks remain common in audio/mcp (partially justified) |
| Silent swallow only on truly inconsequential paths | ⚠️ A few cleanup paths are overly broad |

---

## 3. Modularity and Isolation Integrity

### 3.1 Architectural Intent vs. Reality

Documentation (`architecture-principles.md`) requires:

- plugins access core only via `PluginContext` / `PluginCommands`  
- core does not import concrete implementations of plugins/ui/protocols  
- MCP tools remain as side-effect-free as possible and do not import core (`music_player` is an exception)

**Measured Results**:

| Check Item | Result |
|--------|------|
| `src/core/*` importing plugins/ui | ✅ None found (good isolation) |
| plugins accessing container via Protocol interfaces | ✅ Adapter pattern present |
| MCP tools importing core | ⚠️ `music_player.py` directly imports `from src.core.event_bus import Events` |
| Plugin dependency topology + injection | ✅ `requires` + Kahn topological sort |
| Failed plugin blocking dependents | ❌ **Not implemented** (see H-5) |

### 3.2 High: Isolation Gaps

#### H-5 Downstream plugins still start after a dependency plugin fails ("Half-Dead" system)  
- **Location**:  
  - `AudioPlugin.setup` ~L52-54: sets `self.codec = None` and continues on failure  
  - `WakeWordPlugin.requires = ["audio"]`, `start` logs a warning and returns when codec is missing (`wake_word.py` ~L57-59)  
  - `PluginManager.setup_all` does not evict or propagate failures based on setup results  
- **Symptom**: Microphone/PortAudio initialization fails -> process still enters `wait_shutdown`, UI renders, but core wake-word/conversation capabilities are completely absent; user assumes "the program is running".  
- **Blast Radius**: `audio` is the foundation at priority=10; its failure should be treated as **degraded mode** or a **fatal error**, yet currently it is neither.  
- **Mitigation Direction**:  
  - Define plugin `health`: healthy / degraded / failed;  
  - `requires` dependency failed -> auto-skip downstream plugins;  
  - Optional: call `request_shutdown` or display an explicit degraded banner when critical plugins (audio/ui) fail.

#### H-6 Global singletons span the entire lifecycle; container cannot truly teardown/recreate  
- **Location**:  
  - `ConfigManager` — `src/utils/config_manager.py`, module-level usage  
  - `constants.py` L6: `config = ConfigManager.get_instance()` **import-time side effect**  
  - `McpServer.get_instance()` — `src/mcp/mcp_server.py`  
  - `get_music_player_instance()` — `src/mcp/tools/music/music_player.py` L1288-1297  
  - `ActivationService` / `LoggingConfigManager` follow the same pattern  
- **Symptom**: `ServiceContainer.shutdown` releases the resource pool, but singleton states (tool lists, music codec pointers, injected event_bus) may linger; causes dirty state during testing or hot restarts.  
- **Blast Radius**: Module boundaries are plugins on paper, but a **process-wide shared heap** at runtime.  
- **Mitigation Direction**: Hold singletons in the container and inject them; at minimum provide `reset_for_tests()`; avoid reading config at module import time in `constants`.

#### H-7 Bidirectional Audio <-> Music coupling bypasses plugin boundaries  
- **Location**:  
  - `AudioPlugin.setup` calls `get_music_player_instance().set_audio_codec`  
  - `McpPlugin.setup` injects EventBus into the same MusicPlayer  
  - Both cleanup paths independently stop music (`audio.py` resource cleanup + `mcp.py` resource cleanup)  
- **Symptom**: Shutdown order depends on `resource_pool` registration order; if one side fails, the other still holds a closed codec -> secondary exceptions are swallowed.  
- **Blast Radius**: Shutdown stalls, intermittent "codec is None" playback failures.  
- **Mitigation Direction**: Assign Music ownership to a single plugin; the other side communicates solely via events (`MUSIC_*`) without directly setting the codec.

#### M-4 EventBus string events lack type constraints  
- **Location**: `src/core/event_bus.py` `Events` constants + `emit(event: str, data: Any)`  
- **Symptom**: Misspelled event names silently have no subscribers; payload shape relies on convention.  
- **Blast Radius**: Cross-module refactoring can easily introduce "events emitted with no receiver".  
- **Mitigation Direction**: Keep lightweight; optionally add debug assertions `has_handlers` or typed payload dataclasses (already partially used in UI/music).

#### M-5 UIPlugin directly accesses ViewManager internals  
- **Location**: `src/plugins/ui.py` multiple calls to `self.view_manager.main_model.set_*`, `_emotion_service`  
- **Symptom**: Bloated GUI/CLI if/else branches; renaming View internals breaks the plugin.  
- **Blast Radius**: High UI refactoring cost, though not a primary cause of runtime crashes.  
- **Mitigation Direction**: Unified View facade (partially present via `set_status`/`set_tts_text`).

### 3.3 Well-Implemented Isolation (Negative Findings Noted)

- **PluginManager active isolation** for setup/start/notify/stop: a single plugin exception does not interrupt the loop by default — **by design**.  
- **EventBus `_safe_call`**: a single handler failure does not cancel other handlers — **by design** (missing stack trace is a separate issue).  
- **ResourcePool reverse-order release + continue on single resource failure** — reasonable partial-success shutdown.  
- **Direct protocol audio wiring** (`set_audio_handler`) bypasses EventBus backpressure — deliberate architectural optimization.  
- **Top-level `main.py`** catches `Exception` with `exc_info=True` — unhandled process-level exceptions have a landing spot.

---

## 4. Single-Module Fault -> Global Crash / Deadlock

### 4.1 Critical / High

#### C-1 Fire-and-forget `create_task` in protocol layer (explicit anti-pattern in guidelines)  
- **Location**: `src/core/protocol_manager.py` L68-78  
```python
def _on_incoming_json(...):
    asyncio.create_task(self._event_bus.emit(Events.INCOMING_JSON, json_data))

def _on_incoming_audio(...):
    asyncio.create_task(self._incoming_audio_handler(data))  # every audio frame
```  
- **Symptoms** (explicitly called out in `asyncio-guidelines.md`):  
  1. Tasks are not registered with `TaskManager` -> **not cancelled on shutdown**, risking "Event loop stopped before Future completed" or hanging I/O;  
  2. No `done_callback` -> exceptions can become "Task exception was never retrieved";  
  3. **Per-audio-frame** `create_task`: if the handler slows down slightly, the task queue explodes -> rising memory, scheduling latency, and eventual event loop freeze (**process doesn't crash, but is globally unusable**).  
- **Blast Radius**: Entire session audio/JSON pipeline; UI and plugins coexisting in the same process are all starved.  
- **Mitigation Direction**:  
  - JSON: route via `TaskManager.spawn` or merge into an existing serial queue;  
  - Audio: when **direct await is impossible (synchronous callback)**, use a bounded queue + single consumer task, forbidding per-packet `create_task`.

#### H-8 MQTT callback thread -> bare `create_task`  
- **Location**: `src/protocols/mqtt_protocol.py` ~L237-240, L257-260, L570, etc.  
```python
self.loop.call_soon_threadsafe(
    lambda: asyncio.create_task(self._attempt_reconnect(...))
)
```  
- **Symptom**: Untracked tasks just like C-1; additionally, late-bound `rc`/`error_msg` in `lambda` closures can capture wrong values during rapid consecutive disconnects; `asyncio.create_task` inside the thread will raise if the loop is closed, potentially only logging a message.  
- **Blast Radius**: Reconnect storms, lost network error callbacks -> state machine stuck in LISTENING/SPEAKING.  
- **Mitigation Direction**: Use `asyncio.run_coroutine_threadsafe` + store Future; or `call_soon_threadsafe` invoking `TaskManager.schedule_nowait`; bind closure variables via default arguments.

#### H-9 Application idles after critical plugin failure  
- **Location**: `ServiceContainer.run` ~L232-242: no health check after plugins setup/start, proceeds directly to `await self.tasks.wait_shutdown()`  
- **Symptom**: UI setup fails (swallowed by manager) -> no window / no CLI, yet blocks indefinitely in `wait_shutdown`, exitable only via signal. Similar behavior when Audio fails.  
- **Blast Radius**: **Global deadlock (zombie app)** rather than a Python traceback crash.  
- **Mitigation Direction**: Check critical plugins after setup; return exit code 1 on failure, or print an explicit "degraded" notice in CLI.

#### H-10 Thin network error handling  
- **Location**: `container.py` `_on_network_error` ~L351-352  
```python
async def _on_network_error(...):
    self.state.set_keep_listening(False)
```  
- **Symptom**: Does not update `device_state`, does not abort, and does not guarantee a complete recovery path outside of UI (UI subscribes separately to `NETWORK_ERROR`). State may remain SPEAKING/LISTENING.  
- **Blast Radius**: Abnormal button/wake-word behavior after network disconnect until the user manually aborts.  
- **Mitigation Direction**: Uniformly call `set_device_state(IDLE)` + clear `keep_listening` + optional notification on network error.

### 4.2 Medium: Hard Crashes and Boundaries

#### M-6 What the top level can catch vs. cannot catch  
- **Can catch**: `except Exception` in `main.py` L228-233; `ServiceContainer.run` L245-247.  
- **Cannot catch (process-level)**:  
  - **C extension segfaults** in audio callback threads (PortAudio / Opus / webrtc_apm / sherpa-onnx);  
  - Uncaught errors inside `threading.Thread` off the main thread (depending on Python version/configuration);  
  - Native Qt/qasync assertions.  
- **Example Location**: `audio_codec.py` `_input_callback` / `_output_callback` have Python try blocks, but native numpy/opus errors can abort the process directly.  
- **Mitigation Direction**: Keep callbacks as thin as possible; fail-fast on native library initialization failure; rely on OS-level crash logs if a native crash occurs.

#### M-7 UI event handlers do not wrap themselves in try blocks  
- **Location**: `src/plugins/ui.py` `_press` / `_send_text` / `_abort`, etc.  
- **Symptom**: Exceptions rely on EventBus `_safe_call` as a fallback (see H-1); if someone awaits these methods directly in the future, exceptions will bubble up.  
- **Blast Radius**: Currently caught by EventBus so it won't crash globally; however, combined with H-1 it results in "unresponsive buttons with no stack trace".

#### M-8 `constants.py` import-time side effects  
- **Location**: L6 `config = ConfigManager.get_instance()`  
- **Symptom**: Importing `constants` anywhere initializes config directories / reads from disk; tests or utility scripts unexpectedly trigger I/O; corrupted config causes the import chain to fail -> **global failure early in startup**.  
- **Mitigation Direction**: Lazy-load config inside `get_frame_duration` (function already has this path; module-level call should be removed).

#### M-9 Resource cleanup failure lacks stack trace + continues  
- **Location**: `resource_pool.py` L49-55  
- **Symptom**: An exception in a cleanup callback is swallowed (without stack trace) while remaining resources continue releasing — proper partial shutdown, but makes leaks hard to diagnose.  
- **Mitigation Direction**: Add `exc_info=True`.

### 4.3 Paths That Will Not Globally Crash from a Single Module Exception (Negative Findings)

Based on code search and review, the following paths **will not** kill the process by default when a single plugin raises an exception:

- Plugin setup/start/notify (manager boundary)  
- EventBus handler (`_safe_call`)  
- Tasks spawned via `TaskManager.spawn` (done callback logs the error without re-raising to the loop's default handler — depending on whether other references exist)  
- MCP tool call (`mcp_server._handle_tool_call` catches and sends `_reply_error`)

**Exception**: Unhandled exceptions in untracked `create_task` calls rely on asyncio's default "Task exception was never retrieved" logging and **do not guarantee** business recovery.

---

## 5. Severity Summary Table

| ID | Severity | Theme | One-Liner | Location |
|----|--------|------|--------|------|
| C-1 | **Critical** | Cascading/Deadlock | Protocol JSON/audio per-message `create_task` can accumulate and freeze the loop | `protocol_manager.py:68-78` |
| H-1 | **High** | Exception Swallowing | EventBus swallows exceptions without stack traces | `event_bus.py:166-178` |
| H-2 | **High** | Swallowing + Isolation | Plugin failure lacks stack trace and `failed` marker | `manager.py:150-203` |
| H-3 | **High** | Exception Swallowing | Task exception `exc_info=True` may lose traceback | `task_manager.py:94-99` |
| H-4 | **High** | Exception Swallowing | Container/protocol critical paths lack stack traces | `container.py:362-377`, etc. |
| H-5 | **High** | Isolation | System continues idling after audio fails | `audio.py:52-54` + container run |
| H-6 | **High** | Isolation | Global singletons break container lifecycle | config/mcp/music `get_instance` |
| H-7 | **High** | Isolation | Bidirectional Audio<->Music coupling | `audio.py` + `mcp.py` + `music_player` |
| H-8 | **High** | Cascading | Bare `create_task` / closures in MQTT thread | `mqtt_protocol.py:237+` |
| H-9 | **High** | Cascading | Zombie `wait_shutdown` after critical plugin failure | `container.py:232-242` |
| H-10 | **High** | Cascading | Network error does not reset device state | `container.py:351-352` |
| M-1..M-9 | **Medium** | Mixed | Silent `pass`, UI coupling, C crash surface, etc. | See above |
| — | Low | — | CancelledError `pass`, QueueEmpty `pass`, SIGTRAP `pass`, etc. | Multiple locations, acceptable |

---

## 6. Recommended Priorities (Directional Only, Non-Redesign)

### P0 (Immediate)
1. **Eliminate hot-path fire-and-forget**: switch `protocol_manager` audio to a bounded queue; route JSON through `TaskManager.spawn`.  
2. **Standardize `exc_info` on critical paths**: EventBus, PluginManager, ResourcePool, container JSON handler, TaskManager done callback.  
3. **Critical plugin health latch**: audio/ui setup failure -> explicit exit or degraded banner, forbidding silent zombies.

### P1
4. Plugin `_failed` marker + dependency skip.  
5. Route all cross-thread tasks in MQTT/GPIO/CLI through TaskManager or trackable Futures via `run_coroutine_threadsafe`.  
6. Uniformly reset state to IDLE on network errors.

### P2 (Implemented)
7. Container bind/unbind + plugin injection for MusicPlayer/McpServer  
8. Lazy-load ConfigManager in `constants.py`  
9. Consolidate UI facade  
10. Trackable CLI/GPIO event scheduling  

### P3 / Wrap-up (Implemented)
11. Inject container `TaskManager` into CLI/GPIO/GUI; prefer `schedule_nowait` in `_safe_emit`  
12. Add `exc_info` to `logger.error` on WebSocket/MQTT hot paths  
13. `ConfigManager.reset_instance()` test hook  
14. Startup optimization: defer Settings/OpenCV scanning; lazy-load SettingsModel  
15. Trackable cancellation for lyrics task; use `get_running_loop` + `create_task` in GUIActivation  

### Deliberately Retained / Cannot Be Completely Eliminated in Python Code
- **ConfigManager process singleton**: authoritative source for global configuration; only exposes reset for tests  
- **Native C extension segfaults** (PortAudio/Opus/ONNX): cannot be caught by Python boundaries  
- **MCP tools retrieving MusicPlayer via `get_instance`**: compatibility path, bound to container instance at runtime  

---

## 9. Fix Verification Matrix (C/H)

| ID | Status | Implementation Highlights |
|----|------|----------|
| C-1 | ✅ | Bounded audio queue + JSON `TaskManager.spawn` |
| H-1 | ✅ | EventBus `exc_info=True` |
| H-2 | ✅ | `mark_failed` + dependency skip + stack trace |
| H-3 | ✅ | `exc_info=exc` |
| H-4 | ✅ | Stack traces in container/resource_pool/protocols |
| H-5 | ✅ | audio `mark_failed` + health latch |
| H-6 | ✅ | Music/MCP bind; Config retains singleton + reset |
| H-7 | ✅ | Injection + detach lifecycle |
| H-8 | ✅ | MQTT `_schedule_coro` |
| H-9 | ✅ | Critical plugin health check exit 1 |
| H-10 | ✅ | Network error -> IDLE |

---


## 7. Three Major Themes Comparison (Post-Fix Review)

### (a) Exceptions Swallowed, Unraised, or Undiagnosable
- **Fixed**: Core paths (EventBus / PluginManager / TaskManager / ResourcePool / protocol hot paths) uniformly include `exc_info`; silent paths in audio now log warnings/debug info.  
- **Absent**: Bare `except:` (0 across the entire repository).  
- **Residual**: Peripheral modules (settings / activation / some MCP tools) still have `logger.error(f"...{e}")` calls without stack traces, representing observability debt rather than architectural flaws.  
- **Intentional Swallowing**: Plugin/event isolation remains deliberate; now paired with `mark_failed` and stack traces.

### (b) Incomplete Modular Isolation
- **Improved**: Plugin `failed` state + dependency skip; container bind/unbind for Music/MCP; UI facade; TaskManager injected into UI.  
- **Retained (Reasonable)**: ConfigManager process singleton; MCP tools using `get_*_instance` compatibility entry points (resolved to container instances at runtime).  
- **EventBus**: Still uses string event names + `Any` data (low priority).

### (c) Module A Exception Causing Global Crash
- **Fixed**: Bounded protocol audio queue; critical plugin latch exit 1; network error returns to IDLE; trackable MQTT scheduling.  
- **Startup Experience**: Deferred Settings/OpenCV scanning reduced cold start from ~55s to ~2s (see benchmark logs).  
- **Still Possible**: Native C extension segfaults; lyrics/playback tasks now support trackable cancellation.  
- **Conclusion**: Primary architectural paths leading to a "process alive, product dead" state are closed; remaining items are polish and engineering closure.

---

## 8. Appendix: Search and Sampling Evidence (Initial Audit Snapshot, Pre-Fix)

### 8.1 Commands / Patterns (2026-07-19 Initial Scan)
```
except Exception            -> ~275
except + logger w/o exc_info -> ~182
except ...: pass            -> ~29
asyncio.create_task         -> ~20 (including legitimate self-managed tasks)
get_instance(               -> ~32 call sites
threading.Thread            -> ~7
```

### 8.2 Sampled Source Code Verification (Initial Audit)

| File | Lines | Pattern |
|------|------|------|
| `src/core/event_bus.py` | 172-178 | Fixed: `exc_info` |
| `src/core/task_manager.py` | 94-99 | Fixed: `exc_info=exc` |
| `src/plugins/manager.py` | 150-162 | Fixed: `failed` + stack trace |
| `src/core/protocol_manager.py` | 68-78 | Untracked `create_task` |
| `src/plugins/audio.py` | 52-54, 153-154 | Soft fail + `pass` |
| `src/bootstrap/container.py` | 245-249, 351-352, 362-377 | `run` fallback; network/JSON handling |
| `src/protocols/mqtt_protocol.py` | 237-240 | Thread + `create_task` |
| `src/constants/constants.py` | 6 | Import-time singleton |
| `src/core/resource_pool.py` | 49-55 | Cleanup catch without stack trace |
| `main.py` | 163-164, 231-233 | Intentional `pass` + top-level fallback |

### 8.3 Notes
- The Trellis scaffolding has been removed from the project; this report and `deep-audit.md` serve as standalone audit/remediation records and are not tied to `.trellis/`.

---

*This report is a static code analysis and remediation record. Dynamic behavior is governed by runtime logs and `tests/test_resilience_fixes.py`.*
