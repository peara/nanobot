---
name: test-async-applications
description: Testing async Python applications with external dependencies like databases and context stores
---

## What I do

I help write tests for async Python applications that interact with external services, databases, or complex state management systems.

## When to use me

Use this when:
- Writing unit tests for async code that uses ContextStore, SQLite, or similar dependencies
- Mocking fails because of initialization timing issues
- Data persists before mocks are applied
- Multiple import paths create patching conflicts

## Key Principles

### 1. Mock at the boundary layer
Patch methods called by your code under test, not internal implementation details:
```python
# Good: mock high-level method
with patch.object(bot.contexts, "get", return_value=expected_data):
    # Test runs with pre-mocked context retrieval
```

### 2. Understand initialization timing
Mocks must be active before objects that depend on them are created:
```python
# Wrong: ContextStore already initialized
bot = BotCore(config=config, channels={"telegram": channel})
with patch("sqlite3.connect"):
    # Too late - ContextStore used real connection during init
```

### 3. Prefer method-level mocks over module patches
```python
# More reliable than patching sqlite3.connect directly
patch.object(instance, "method")
```

## Common Patterns in This Codebase

### ContextStore pattern
- Stores data in SQLite via internal `_connect()` method
- Test should mock `contexts.get()` or patch before BotCore initialization
- Don't try to mock sqlite3 after ContextStore already created its connection

### Async test pattern
```python
asyncio.run(bot.on_incoming(message))  # Execute async handler
assert channel.sent[0][1] == expected   # Verify result
```

## Debugging Checklist

When tests fail with unexpected behavior:
- [ ] Check what's actually being called - add logging or print statements
- [ ] Verify mock scope - is the patch active when the code runs?
- [ ] Look for multiple import paths - same function imported differently in different modules
- [ ] Consider initialization order - objects created before mocks won't use them

## Repo-Specific Patterns

- Commands inherit from `BaseCommand` and call `self._send()` or `await self.handle()`
- ContextStore uses scoped storage with `(scope_type, scope_id, key)` tuples
- Fake classes (`_FakeChannel`, `_FakeLlm`) follow the same interfaces as real implementations