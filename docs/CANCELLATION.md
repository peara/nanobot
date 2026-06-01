# Cancellation

How nanobot cancels in-flight work — the pattern, the call chain, and why we chose explicit tokens over asyncio's native `Task.cancel()`.

## The problem

When the bot receives SIGINT/SIGTERM, `BotCore.stop()` cancels the message queue task, but in-flight LLM calls continue running until they complete or timeout — up to 600 seconds via `httpx.Timeout(600.0)`. There is no mechanism to abort an active `LlmClient.chat()` call, the `AgentRun.run()` tool loop, or any subagent execution.

The same problem applies per-scope: a future `/stop` command needs to cancel a specific request without stopping the entire bot.

## The pattern: explicit CancellationToken

We use a hierarchical `CancellationToken` that threads through the call chain as an explicit parameter. When a token is cancelled, every function holding it can check `is_cancelled` and abort cooperatively.

### Why not `asyncio.Task.cancel()`?

Python's native cancellation works at the **task boundary** — `cancel()` injects `CancelledError` at the next `await` in a specific task. This is clean when each unit of work runs as its own `asyncio.Task`, but nanobot processes requests **sequentially within a single task** (`_process_queue_loop`). There is no per-request task to cancel.

The alternative — refactoring to per-request tasks — would enable `Task.cancel()`, but introduces concurrency (multiple LLM calls in flight simultaneously) and shared-state concerns (SQLite writes, `active_requests` dict mutations). For a personal bot designed around sequential processing due to resource constraints, per-request tasks change the execution model for the sole purpose of cancellation. The explicit token achieves cancellation without restructuring.

The other alternative — `contextvars` — also requires per-request tasks, since context is per-task (not per-coroutine within a shared task).

### Why a custom exception instead of `CancelledError`?

`asyncio.CancelledError` is a `BaseException`, not an `Exception`. This means:

- Any `except Exception` handler **intentionally** does not catch it (correct behavior most of the time)
- But if you catch `CancelledError` for cleanup, you **must** re-raise it — otherwise the task silently reports as "completed" instead of "cancelled". This is a well-known footgun.
- External libraries (like the openai client) may have internal `try/except` blocks that suppress or mishandle `CancelledError`.

Our `LlmCallCancelledError` is a regular `Exception`. It's caught explicitly at the right layer, never accidentally suppressed, and never requires re-raising. It also carries a `scope` for logging — something `CancelledError` can't do.

## The call chain

```
BotCore._process() / _handle_scheduled_task_message()
  └── token = CancellationToken()
  └── self._cancel_tokens[scope] = token
  └── SubagentManager.execute(cancel_token=token)
        └── AgentRun.run(cancel_token=token)
              └── check token at entry and each loop iteration
              └── LlmClient.chat(cancel_token=token)
                    └── if cancelled before call → raise LlmCallCancelledError
                    └── if cancelled during call → race LLM HTTP request against token.wait()
```

Four hops. The token is created in `BotCore`, passed through `SubagentManager` and `AgentRun`, and consumed in `LlmClient`.

## Cancellation points

| Layer | Check | What happens |
|-------|-------|--------------|
| `AgentRun.run()` | Entry (`if cancel_token.is_cancelled`) | Raises before any work starts |
| `AgentRun.run()` | Top of while loop | Raises before processing next round of tool calls |
| `LlmClient.chat()` | Before HTTP call | Raises immediately, no network I/O |
| `LlmClient.chat()` | During HTTP call | Races `client.chat.completions.create()` against `cancel_token.wait()` via `asyncio.wait(FIRST_COMPLETED)` |

Tool execution (`self._host.tools.call()`) does **not** check the token. MCP tool cancellation is future work — tool calls are typically fast, and the token check at the next loop iteration catches cancellation after a tool returns.

## The LLM call race

The critical code in `LlmClient.chat()`:

```python
if cancel_token is None:
    response = await self.client.chat.completions.create(...)
else:
    llm_task = asyncio.ensure_future(self.client.chat.completions.create(...))
    cancel_waiter = asyncio.ensure_future(cancel_token.wait())
    done, pending = await asyncio.wait(
        [llm_task, cancel_waiter],
        return_when=asyncio.FIRST_COMPLETED,
    )
    for t in pending:
        t.cancel()
    for t in list(pending):
        try:
            await t
        except asyncio.CancelledError:
            pass
    if cancel_token.is_cancelled:
        raise LlmCallCancelledError(scope=scope)
    response = llm_task.result()
```

When `cancel_token` fires, `cancel_waiter` completes, the LLM task is cancelled, and `LlmCallCancelledError` is raised. When the LLM responds first, `cancel_waiter` is cancelled (harmless), and the response is processed normally.

We race at the HTTP level rather than using `Task.cancel()` on the whole request task because we don't control the openai library's internal exception handling. If openai internally catches `CancelledError` (a `BaseException`), it could suppress the cancellation silently. Our explicit race gives us full control over the cancellation boundary.

## Shutdown

`BotCore.stop()` cancels all tokens before cancelling the queue task:

```python
async def stop(self) -> None:
    for token in self._cancel_tokens.values():
        token.cancel()
    if self._queue_task is not None:
        self._queue_task.cancel()
        ...
    await self.scheduler.stop()
    await self._mcp_source.stop()
```

Tokens are cancelled first so in-flight requests self-terminate gracefully. The queue task is then cancelled to stop accepting new work.

## Per-scope cancellation

`BotCore.cancel_request(scope)` cancels a specific scope's token — the foundation for a future `/stop` command:

```python
def cancel_request(self, scope: str) -> bool:
    token = self._cancel_tokens.get(scope)
    if token:
        token.cancel()
        return True
    return False
```

## CancellationToken hierarchy

The token supports parent-child linking for future subagent-owns-children cancellation:

```python
parent_token = CancellationToken()
child_token = parent_token.create_child()

parent_token.cancel()  # cascades to child
child_token.cancel()   # does NOT cancel parent
```

When subagents can spawn children, a parent token's `cancel()` cascades to all descendants. A child token's `cancel()` only cancels that child and its descendants — the parent and siblings are unaffected.

This is unused today (subagents don't have their own children yet), but the API supports it without changes.

## Implementation reference

| File | Role |
|------|------|
| `src/nanobot/cancel_token.py` | `CancellationToken`, `LlmCallCancelledError` |
| `src/nanobot/llm.py` | Race LLM call against token, raise on cancellation |
| `src/nanobot/agent_run.py` | Check token at entry, loop iteration; pass to LLM calls |
| `src/nanobot/subagents/manager.py` | Pass token through, catch `LlmCallCancelledError`, set status `"cancelled"` |
| `src/nanobot/core.py` | Create per-scope tokens, register in `_cancel_tokens`, cancel all on `stop()`, `cancel_request()` |

## Alternatives considered

| Approach | Why not |
|----------|---------|
| `asyncio.Task.cancel()` per request | Requires per-request tasks → concurrent execution → shared-state concerns. Changes the execution model for cancellation alone. |
| `contextvars.ContextVar` | Per-task isolation, not per-coroutine. Requires per-request tasks (same issue as above). |
| `anyio.CancelScope` | Scope-bound (context manager), not passable as a parameter. Designed for structured concurrency within a single task. LIFO scoping incompatible with our call chain. |
| `aiofence` | Go `context.Context`-inspired, but explicitly no nesting support. v0.0.2 with pydantic dependency. |
| Third-party `CancellationToken` libraries | Add dependencies for ~60 lines of code. Our implementation is zero-dependency and exactly fits our needs. |