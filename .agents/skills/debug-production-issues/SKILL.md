# Debugging Production Issues

**Generated:** 2026-04-26
**Trigger:** intelligent

## When to Use

Use this skill when debugging issues where:
- Tests pass but production fails
- Data not persisting across restarts
- Reports of success but no actual effect
- Lock conflicts or resource contention
- External library integration mysteries
- Contradictory evidence between sources

## Core Principle: Ground Truth First

**Before any investigation**, verify the actual state directly:

1. Check if process is running (or zombie/stale)
2. Check storage directly (not through library APIs)
3. Check via actual client (not just storage files)
4. Check timestamps (storage files vs process lifetime)

```bash
# Process check
pgrep -f "python.*yourapp" && echo "RUNNING" || echo "NOT RUNNING"

# Storage check (bypass library)
sqlite3 data/storage.sqlite "SELECT COUNT(*) FROM records;"

# Client check (through library)
python -c "from lib import Client; c = Client(path='...'); print(c.count())"

# Timestamps
ls -la data/storage.sqlite
```

## Pattern: Tests Pass, Production Fails

**Wrong assumption**: "Tests pass, so the code works"

**Reality**: Tests may bypass the failing code path via mocking

```python
# Test code (bypasses real issue)
with patch.object(Library, "get_connection", return_value=mock_conn):
    obj = Library(config)  # Real connection code never runs!

# Production code (runs real path)
obj = Library(config)  # Creates real connection → failure!
```

**Debug approach**:
1. Find what the test mocks
2. Run that exact code path in isolation
3. Compare test fixture vs production config

## Pattern: Multiple Clients on Same Resource

External libraries often create internal instances you don't control:

```python
# Your code creates one client
self._client = Client(path="/data/storage")

# Then library creates ANOTHER internally
library_obj = Library.from_config(config)  # May create Client(path="/data/storage") again!

# Result: lock conflict or resource exhaustion
```

**Debug approach**:
1. Trace all objects created (add print/logging for object IDs)
2. Check if library accepts injected client via config
3. If not, consider bypassing library for direct operations

## Pattern: Library Returns Success, No Effect

Libraries may catch exceptions internally and return "success":

```python
# Library code (hidden from you)
def add(self, data):
    try:
        self._internal_client.store(data)
    except LockError:
        return {"results": []}  # Swallowed!
    return {"results": [{"id": "..."}]}

# Your code sees success
result = library.add(data)  # {"results": []}
# Looks like success, but nothing was stored
```

**Debug approach**:
1. Add DEBUG level logging to the library
2. Check actual storage after every library call
3. Run library operation in fresh process (no locks)

## Pattern: Stale Locks and Zombie Processes

If using file-based storage (SQLite, local vector DBs, etc.):

```bash
# Check for lock files
find data/ -name "*.lock" -o -name "*.pid"

# Check what's holding the file
lsof data/storage/

# Check for zombie processes
ps aux | grep -E "defunct|zombie"
```

**Common causes**:
- Previous process didn't exit cleanly
- Multiprocessing without proper cleanup
- Testing while production process running

## Pattern: Config Affects Behavior Invisibly

Production and test configs differ in invisible ways:

```yaml
# test_config.yaml (works)
storage:
  path: /tmp/test-12345/  # Fresh directory, no locks

# production_config.yaml (fails)  
storage:
  path: /data/storage/    # May have stale locks
  on_disk: true            # Enables persistence (and locking)
```

**Debug approach**:
1. Compare configs word-by-word
2. Test with production config in isolation
3. Test with test config in production environment

## Debugging Checklist

Before deep investigation, run these checks:

1. [ ] Is process running? (`pgrep`, `ps aux`)
2. [ ] What does storage show directly? (`sqlite3`, file read)
3. [ ] What does client API show? (library methods)
4. [ ] Do timestamps match expectations? (`ls -la`, `stat`)
5. [ ] Are there stale locks? (`find *.lock`, `lsof`)
6. [ ] Do tests mock the suspect code path? (grep for `patch.object`)
7. [ ] Does library accept dependency injection? (check config options)
8. [ ] What does DEBUG logging show? (enable library debug mode)

## Integration Test Anti-patterns

Tests that miss production bugs:

```python
# WRONG: Mock the integration point
with patch.object(Store, "connect", return_value=mock_conn):
    store = Store(config)  # Real connect() never runs

# WRONG: Use in-memory variants
client = Client(":memory:")  # Different behavior than file-based

# RIGHT: Use real paths with isolated test data
test_dir = tmp_path / "test_storage"
store = Store(config_path=test_dir / "config.yaml")
store.save(data)
# Verify directly
assert (test_dir / "storage.db").exists()
```

## The Fresh Instance Test

When nothing makes sense, test from clean state:

```bash
# 1. Stop all processes
pkill -f "yourapp"

# 2. Clear all storage
rm -rf data/storage/*
rm -f data/*.lock

# 3. Run single operation in fresh process
python -c "
from yourlib import Client
c = Client(config)
c.save('test')
print(c.count())  # Should be 1
"

# 4. Check storage directly
ls -la data/storage/
sqlite3 data/storage/db.sqlite "SELECT COUNT(*) FROM records;"
```

**If fresh instance works** → issue is state/lock/resource contention
**If fresh instance fails** → issue is config/code logic

## Real Example: The 4-Hour Debug

**Symptoms**:
- `resync` reports `[OK]` but storage has 0 records
- Integration tests pass
- Search returns empty results

**Wrong paths explored**:
1. "Tests pass, assume storage works" → tests mocked the real code path
2. "Maybe config flag `on_disk` is wrong" → checked config, was correct
3. "Check if data persists after restart" → still 0, but wrong assumption about cause
4. "Probably the library integration" → correct direction, but slow to trace

**Correct approach (hindsight)**:
1. Check storage directly immediately → confirmed 0 records
2. Run `resync` with DEBUG logging → revealed exception being caught
3. Trace library object creation → found duplicate client instance
4. Check for lock conflict → found `AlreadyLocked` error
5. Inject shared client → fix

**Time saved by correct approach**: ~3 hours

## When to Stop and Consult

Escalate to Oracle when:
- 3+ hypotheses tested with no progress
- Library behavior contradicts documentation
- Need architectural decision (bypass library vs fix integration)
- Root cause seems to be in external dependency

## Success Pattern Summary

```
1. Ground truth: What does storage actually contain?
2. Process state: Is anything holding the resource?
3. Code path: Do tests test the real path or a mock?
4. Object instances: Are there duplicates on same resource?
5. Library internals: Where does it create hidden objects?
6. Fresh instance: Does clean state work?
7. Inject dependencies: Can we share resources?
8. Verify end-to-end: Run exact user command, check exact user path
```

## Anti-patterns

1. **Assuming without checking**: "Config is correct" → check it anyway
2. **Trusting library return values**: `{results: []}` may indicate failure
3. **Mocking integration points**: Tests the mock, not the code
4. **Debugging while process runs**: Locks cause false negatives
5. **Changing multiple things at once**: Can't isolate cause
6. **Believing "it should work"**: Reality beats theory