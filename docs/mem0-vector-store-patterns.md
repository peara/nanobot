# Vector Store / Qdrant Patterns

## The Problem: Multiple QdrantClient Instances on the Same Data Directory

Qdrant in local mode uses file-based locking (`data/<path>/.lock`). When two `QdrantClient`
instances point to the same `path`, they **contend on writes**, producing lock errors like:

```
qdrant_client.core.exceptions.ResponseHandlingException: 
  Unexpected response: 409 Conflict: Resource qdrant lock file
```

This happens silently — one client's write succeeds while the other returns empty results
or swallows the exception.

## Anti-Pattern: Shared Config Path

```python
# BotCore creates one VectorStore with mem0 config
self.vector_store = VectorStore(config.mem0_config_path)
# → QdrantClient(path="./data/mem0/qdrant")

# MCP server creates ANOTHER VectorStore with the SAME config
# via env var fallback:
return os.environ.get("WEB_SCRIPT_VECTOR_CONFIG") or os.environ.get("MEM0_CONFIG_PATH")
# → QdrantClient(path="./data/mem0/qdrant")  ← SECOND CLIENT, SAME DIR
```

Even though `VectorStore` is a different Python object, both hold a `QdrantClient`
pointing to the same on-disk database. Writes from either client contend for the
`.lock` file.

## Pattern: Separate Config Per Consumer

Each `VectorStore` consumer must use a config with a **unique Qdrant path**:

```
config.mem0.yaml          → vector_store.config.path: "./data/mem0/qdrant"
config.web-scripts.mem0.yaml → vector_store.config.path: "./data/web_scripts/qdrant"
```

Each config file specifies its own `path`, so each `QdrantClient` gets its own
data directory and lock file. No contention.

## Pattern: Constructor Injection (Preferred)

When multiple consumers need the **same** Qdrant instance (same collections), pass
the `VectorStore` object by reference — don't create a new one from the same config:

```python
# Correct: share the same VectorStore instance
class SkillVectorStore:
    def __init__(self, vector_store: VectorStore) -> None:  # injected
        self._store = vector_store
        self._store.ensure_collection(COLLECTION_SKILLS)

# Also correct: WebScriptVectorStore receives injected VectorStore
class WebScriptVectorStore:
    def __init__(self, vector_store: VectorStore) -> None:  # injected
        self._store = vector_store
        self._store.ensure_collection(COLLECTION_WEB_SCRIPTS)
```

Both `SkillVectorStore` and `WebScriptVectorStore` can share one `VectorStore`
instance because they use **different collections** within the same QdrantClient.
The lock file protects directory-level writes, not collection-level operations,
so sharing a single client is safe and preferred.

## Current Architecture

```
config.mem0.yaml
  └── BotCore.__init__
        └── VectorStore(config_path)        ← ONE QdrantClient
              ├── SkillVectorStore (collection: nanobot_skills)
              └── mem0 Memory (collection: nanobot_memories)

config.web-scripts.mem0.yaml
  └── _build_script_vector_store()
        └── VectorStore(config_path)         ← SECOND QdrantClient (separate dir)
              └── WebScriptVectorStore (collection: nanobot_web_scripts)
```

Two QdrantClients, two data directories, two lock files. No contention.

## Decision Record

- **Why not share one VectorStore for everything?** The web MCP server runs as an
  MCP subprocess and cannot receive injected Python objects from BotCore's process.
  The cleanest boundary is a separate config with a separate data directory.

- **Why not share the Qdrant data directory with different collections?** Collection
  operations are safe under a shared client, but the client creation itself acquires
  a directory-level lock. Two `QdrantClient(path=same_dir)` instances will contend.
  Use separate paths for separate client lifecycle boundaries.

- **When CAN you share a VectorStore?** Within the same process (e.g., BotCore),
  where you can pass the Python object by reference. `SkillVectorStore` and
  `mem0 Memory` share `self.vector_store` safely because they use different
  collections under one client.

## Consumers and Their Configs

| Consumer | Config File | Qdrant Path | Collections |
|----------|-------------|-------------|-------------|
| BotCore (mem0, skills) | `config.mem0.yaml` | `./data/mem0/qdrant` | `nanobot_memories`, `nanobot_skills` |
| Web scripts | `config.web-scripts.mem0.yaml` | `./data/web_scripts/qdrant` | `nanobot_web_scripts` |

## Checklist: Adding a New VectorStore Consumer

1. **Same process as BotCore?** → Inject `self.vector_store` by reference.
2. **Separate process (MCP server)?** → Create a dedicated config with a unique Qdrant path.
3. **Never** fall back to `MEM0_CONFIG_PATH` for a different consumer.
4. **Never** create a second `VectorStore(config_path)` pointing at an existing Qdrant directory.
5. Verify the lock file is in a unique directory: `ls -la data/<consumer>/qdrant/.lock`.