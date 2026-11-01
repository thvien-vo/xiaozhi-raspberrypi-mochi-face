# py-xiaozhi Deep Audit Report (Post-Fix Review)

**Date**: 2026-07-19  
**Scope**: `main.py` + `src/` (reliability / exceptions / isolation / cascading); compared against P0-P3 fixes in this round  
**Methodology**: Static scanning + critical path source code verification + unit testing (`tests/test_resilience_fixes.py` **15 passed**)  
**Nature**: Audit conclusion; does not include a new round of major refactoring (except for the documentation itself)

---

## 0. One-Sentence Conclusion

| Dimension | Rating | Description |
|------|------|------|
| **Architectural Reliability (Original C/H)** | **Good** | Protocol hot paths, plugin health latches, EventBus/Task stack traces, and Music/MCP bindings are all in place |
| **Repository-Wide Consistency** | **Medium** | Peripheral modules still have ~149 instances of logging `{e}` without stack traces, plus local fire-and-forget tasks |
| **Process Safety** | **Medium-High** | Python exceptions are mostly caught at boundaries; gaps remain in C extensions and activation GUI async bridges |
| **Engineering Closure** | **Incomplete** | Changes remain in the working tree (`main` ahead 1 + large unstaged changes) and have not yet formed a complete releasable commit |

**"System-hanging" issues from the original risk report have been largely eliminated at the code level.**  
**What remains is observability debt, localized task leaks, documentation lag, and uncommitted code assets.**

---

## 1. Fix Implementation Verification (Quality Gate)

All of the following key fixes are **present** in the source code (static marker verification OK):

| Fix | Evidence Location |
|------|----------|
| Bounded inbound audio queue + single consumer | `protocol_manager.py` `_audio_queue` / `_audio_consumer` |
| JSON routed through TaskManager.spawn | `ProtocolTransport._spawn` |
| TaskManager `exc_info=exc` | `task_manager.py` done callback |
| EventBus `exc_info=True` | `event_bus.py` `_safe_call` |
| Plugin `mark_failed` + dependency skip | `base.py` / `manager.py` |
| Critical plugin health -> exit 1 | `container._check_critical_plugins` |
| Network error resets to IDLE | `container._on_network_error` |
| Music/MCP bind/unbind | `music_player.py` / `mcp_server.py` / `container._bind_shared_services` |
| constants without import-time Config | No `get_instance` at top of `constants.py` |
| UI TaskManager injection | `UIPlugin` -> GUI/CLI/GPIO |
| Startup optimization / lazy-loaded Settings | `settings_model` / `ViewManager` |

---

## 2. Repository-Wide Scan Snapshot (Current)

| Pattern | Count | Interpretation |
|------|------|------|
| `except Exception` | **293** | Still high; boundary isolation culture — the issue lies in quality rather than quantity |
| `logger.*{e}` **without** `exc_info` | **~149** | Down from ~182 before fixes, but still common in peripheral modules |
| `except ...: pass` | **~34** | Mostly CancelledError / QueueEmpty, largely reasonable |
| Bare `except:` | **0** | Compliant |
| `asyncio.create_task` | **11** | 10 legitimately self-managed + **1 real issue** (lyrics) |
| `ensure_future` | **1** | `gui/activation.py` |
| `get_event_loop` | **1** | Same as above |
| `get_instance` | **~33** | Config/Camera/Activation etc. remain global |
| `threading.Thread` | **7** | Settings page audio/camera tests + MQTT UDP + activation voice prompt |

### Top Files Logging Without Stack Traces

| Count | File | Risk |
|------|------|------|
| 15 | `mcp/tools/music/music_player.py` | Hard to troubleshoot playback pipeline |
| 9 | `audio_codecs/audio_codec.py` | **Real-time callback thread**, trade-off between log flooding and observability |
| 8 | `mcp/tools/screenshot/...` | Tool side |
| 7 | `ui/shared/models/settings_model.py` | Settings page |
| 6 | `music_decoder` / `activation` / `protocol.py` | Medium |

---

## 3. Remaining Issues (By Severity)

### 3.1 Medium — Recommended for Scheduling

#### M-A Lyrics task fire-and-forget — **Fixed**  
- `_lyrics_task` is now trackable; uniformly cancelled in `stop` / `_start_playback`; done callback logs stack trace  

#### M-B GUI Activation: `get_event_loop` + `ensure_future` — **Fixed**  
- Changed to `get_running_loop()` + `loop.create_task` 

#### M-C Audio callback path logs without stack traces  
- **Location**: `audio_codec.py` input/output callback ~147-158, encoding failures, etc.  
- **Symptom**: Only logs `str(e)` on real-time threads; genuine exceptions are difficult to pinpoint  
- **Impact**: Field troubleshooting for "no sound" issues relies on guesswork  
- **Note**: Adding `exc_info` on hot paths may flood logs; recommend **stack traces on error, rate-limiting on warning**  

#### M-D Settings page worker threads lack a unified exception exit  
- **Location**: `settings_model.py` 4x `threading.Thread` (recording/playback/camera)  
- **Symptom**: Worker thread exceptions rely on in-thread try blocks; if Qt Signal callback fails, the UI spinner may spin indefinitely  
- **Mitigation**: Unified try/except at thread entry + Signal error state  

#### M-E Peripheral `exc_info` debt  
- Approximately 149 places; non-core paths, though settings / activation / music affect user perception  
- Can be swept file-by-file in batches without changing everything at once  

### 3.2 Low — Technical Debt / Consistency

| Item | Description |
|----|------|
| EventBus string events | Typos silently result in no handler matching |
| Config/Camera/Activation singletons | Reasonable at process level; hot restart/testing requires reset |
| MCP tools `get_music_player_instance` | Compatibility layer, already bound to container instance at runtime |
| `risk-analysis.md` Section 7 | Still uses old "failure points" wording, contradicting Section 1 / Section 9 |
| No e2e / GUI smoke tests | Unit tests cover core fixes, but do not cover real-device audio sessions |
| **Changes not fully committed** | Source diffs + untracked tests/risk docs |

### 3.3 Critical / High — No New "System-Hanging" Design Flaws Found

Compared against original C-1, H-1...H-10:

- Per-frame protocol task accumulation -> **Fixed**  
- EventBus/Plugin missing stack trace / missing failed state -> **Fixed**  
- Zombie wait_shutdown -> **Fixed**  
- MQTT bare create_task -> **Fixed**  
- Network error not returning to IDLE -> **Fixed**  

**No architectural flaws of equivalent severity were found in the new scan.**

---

## 4. Architectural Health (Isolation)

```
main -> ServiceContainer
          |- TaskManager  <- UI / Protocol injected
          |- EventBus
          |- ProtocolManager (bounded audio + spawn)
          |- PluginManager (failed + dependency skip)
          |- bind(McpServer, MusicPlayer)
          +- ResourcePool (reverse order; unbinds shared services last)
```

| Check | Result |
|------|------|
| core does not import plugins/ui | ✅ |
| Plugins communicate via ctx/cmd | ✅ |
| Critical plugin failure exits | ✅ |
| Shared service lifecycle | ✅ bind/unbind (not full DI, but controllable) |
| Cross-thread entry into loop | ✅ Main paths use TaskManager / run_coroutine_threadsafe |

**Residual Coupling**: Audio still sets codec onto Music; MCP still sets EventBus — clear separation of responsibilities, though bidirectional touchpoints remain (converged via detach).

---

## 5. Cascading Failure Scenarios (Post-Fix Walkthrough)

| Scenario | Expected Behavior | Confidence |
|------|----------|--------|
| PortAudio initialization failure | audio failed -> latch exit 1 | High (unit test + code path) |
| UI QML load failure | ui failed -> exit 1 | High |
| Single plugin setup raises exception | Marked failed, others continue | High (unit test) |
| EventBus handler raises exception | Isolated + stack trace; other handlers continue | High (unit test) |
| Inbound audio storm | Bounded queue drops oldest frames, single consumer | High (unit test) |
| MQTT disconnect | `_schedule_coro` trackable | Medium (no e2e) |
| Network error | keep_listening=False + IDLE | Medium |
| Music lyrics task leak | **Still possible** residual task | Medium (code review) |
| Native library segfault | Process dies immediately | Known unpreventable |
| Activation GUI ensure_future | Possible anomaly in edge environments | Medium |

---

## 6. Testing and Repository Status

| Item | Status |
|----|------|
| `tests/test_resilience_fixes.py` | **15 passed** (0.2s) |
| Real-device GUI/CLI session | **Not run** |
| Git | Remediation changes need a separate commit (excluding Trellis) |

---

## 7. Recommended Priorities (If Continuing Investment)

### P0 (Small changes, high return)
1. Change lyrics task to `self._lyrics_task` + cancel on stop  
2. `gui/activation.py`: `get_running_loop` + `create_task` (remove ensure_future)  
3. **Commit all fixes from this round** (including tests + reports) to prevent working tree loss  

### P1
4. Add `exc_info` to `audio_codec` callback error paths (rate-limit warnings)  
5. Batch-add stack traces in `music_player` / `settings_model`  
6. Synchronize wording in `risk-analysis.md` Section 7  

### P2
7. Unified error Signal for settings page Threads  
8. Optional: degraded mode (do not exit on audio failure; show UI banner)  
9. e2e smoke script (cli + mock protocol)  

---

## 8. Audit Methodology Appendix

```
Scan: except / create_task / ensure_future / get_event_loop /
      get_instance / Thread / logger*{e} without exc_info
Gate: Existence of 10 key fix markers
Test: pytest tests/test_resilience_fixes.py
```

---

*This report is a post-fix deep review. Architectural C/H issues are closed; Medium items and engineering closure (commits, e2e, logging cleanup) remain.*
