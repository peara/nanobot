from __future__ import annotations

import time

from nanobot.context_store import ContextStore


def test_context_store_put_get_roundtrip(tmp_path) -> None:
    db_path = tmp_path / "nanobot.db"
    store = ContextStore(str(db_path))

    payload = {"goal": "summarize", "steps": [{"id": "s1", "status": "pending"}]}
    store.put("plan_run", "run-1", "state", payload)

    assert store.get("plan_run", "run-1", "state") == payload


def test_context_store_scopes_are_isolated(tmp_path) -> None:
    db_path = tmp_path / "nanobot.db"
    store = ContextStore(str(db_path))

    store.put("chat", "telegram:1", "state", {"kind": "chat"})
    store.put("plan_run", "telegram:1", "state", {"kind": "run"})
    store.put("chat", "telegram:2", "state", {"kind": "chat-2"})

    assert store.get("chat", "telegram:1", "state") == {"kind": "chat"}
    assert store.get("plan_run", "telegram:1", "state") == {"kind": "run"}
    assert store.get("chat", "telegram:2", "state") == {"kind": "chat-2"}


def test_context_store_ttl_expires_entry(tmp_path) -> None:
    db_path = tmp_path / "nanobot.db"
    store = ContextStore(str(db_path))

    store.put("plan_run", "run-1", "ephemeral", {"ok": True}, ttl_seconds=1)
    assert store.get("plan_run", "run-1", "ephemeral") == {"ok": True}

    time.sleep(1.1)
    assert store.get("plan_run", "run-1", "ephemeral") is None


def test_context_store_list_scope_and_cleanup_expired(tmp_path) -> None:
    db_path = tmp_path / "nanobot.db"
    store = ContextStore(str(db_path))

    store.put("plan_run", "run-1", "alive", {"v": 1})
    store.put("plan_run", "run-1", "stale", {"v": 2}, ttl_seconds=1)
    time.sleep(1.1)

    # list_scope filters expired values and returns only live keys.
    assert store.list_scope("plan_run", "run-1") == {"alive": {"v": 1}}

    # cleanup_expired remains idempotent after filtered reads.
    assert store.cleanup_expired() == 0
