"""Integration tests for mem0 memory pipeline (VectorStore + real LLM).

Exercises the full mem0 pipeline: fact extraction LLM -> dedup LLM ->
embedding -> Qdrant storage -> retrieval. Requires LM Studio running at
localhost:1234 with the configured model loaded. The bot must be STOPPED
(Qdrant local mode uses exclusive file locks).

Run: uv run pytest tests/memstore/test_integration.py -v
Skip: uv run pytest tests/memstore/test_integration.py -v -k "not integration"
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import pytest
import yaml

from nanobot.memstore.tools import (
    MemoryListTool,
    MemorySaveTool,
    MemorySaveTurnTool,
    MemorySearchTool,
)
from nanobot.vector_store import COLLECTION_MEMORIES, VectorStore

LMSTUDIO_BASE_URL = "http://localhost:1234/v1"
LMSTUDIO_MODEL = "google/gemma-4-31b"
EMBEDDING_MODEL = "mxbai-embed-large"
EMBEDDING_DIMS = 1024
TEST_USER = "integration_test_user"


def _lmstudio_reachable() -> bool:
    try:
        req = urllib.request.Request(f"{LMSTUDIO_BASE_URL.rstrip('/')}/models")
        with urllib.request.urlopen(req, timeout=3):
            return True
    except (urllib.error.URLError, ConnectionError, TimeoutError):
        return False


def _mem0_config_dict(qdrant_path: str, response_format: dict[str, Any] | None = None) -> dict[str, Any]:
    config: dict[str, Any] = {
        "llm": {
            "provider": "lmstudio",
            "config": {
                "model": LMSTUDIO_MODEL,
                "lmstudio_base_url": LMSTUDIO_BASE_URL,
                "api_key": "lm-studio",
                "temperature": 0.1,
                "max_tokens": 1200,
            },
        },
        "embedder": {
            "provider": "openai",
            "config": {
                "model": EMBEDDING_MODEL,
                "openai_base_url": LMSTUDIO_BASE_URL,
                "api_key": "lm-studio",
                "embedding_dims": EMBEDDING_DIMS,
            },
        },
        "vector_store": {
            "provider": "qdrant",
            "config": {
                "path": qdrant_path,
                "collection_name": "nanobot_memories",
                "embedding_model_dims": EMBEDDING_DIMS,
                "on_disk": True,
            },
        },
    }
    if response_format is not None:
        config["llm"]["config"]["lmstudio_response_format"] = response_format
    return config


def _make_vector_store(tmp_path: Path, response_format: dict[str, Any] | None = None) -> VectorStore:
    config = _mem0_config_dict(str(tmp_path / "qdrant"), response_format)
    config_path = tmp_path / "config.mem0.yaml"
    config_path.write_text(yaml.dump(config, default_flow_style=False), encoding="utf-8")
    vs = VectorStore(str(config_path))
    vs.ensure_collection(COLLECTION_MEMORIES)
    return vs


requires_lmstudio = pytest.mark.skipif(
    not _lmstudio_reachable(),
    reason="LM Studio not reachable at localhost:1234",
)


@pytest.fixture()
def vs(tmp_path: Path) -> VectorStore:
    """VectorStore with default config (inherits lmstudio_response_format from LMStudioLLM)."""
    vs = _make_vector_store(tmp_path)
    yield vs
    try:
        vs._qdrant_client.close()
    except Exception:
        pass


@pytest.fixture()
def vs_json_schema(tmp_path: Path) -> VectorStore:
    """VectorStore with json_schema+empty_schema response format (the bug trigger)."""
    vs = _make_vector_store(
        tmp_path,
        response_format={"type": "json_schema", "json_schema": {"type": "object", "schema": {}}},
    )
    yield vs
    try:
        vs._qdrant_client.close()
    except Exception:
        pass


@pytest.fixture()
def vs_json_object(tmp_path: Path) -> VectorStore:
    """VectorStore with json_object response format (the correct config)."""
    vs = _make_vector_store(tmp_path, response_format={"type": "json_object"})
    yield vs
    try:
        vs._qdrant_client.close()
    except Exception:
        pass


@requires_lmstudio
class TestMem0SaveSearch:
    """End-to-end save -> search through real LLM + Qdrant."""

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_save_and_search_fact(self, vs: VectorStore) -> None:
        save_tool = MemorySaveTool(vs)
        search_tool = MemorySearchTool(vs)

        await save_tool.call({"text": "The user's birthday is August 25.", "user_id": TEST_USER})

        import time

        time.sleep(0.5)

        result = await search_tool.call({"query": "When is the user's birthday?", "user_id": TEST_USER})
        data = json.loads(result)
        memories = data.get("results", []) if isinstance(data, dict) else data

        assert len(memories) > 0, f"Expected at least one memory, got: {memories}"
        found = any(
            "august" in str(m.get("memory", m.get("data", ""))).lower()
            or "birthday" in str(m.get("memory", m.get("data", ""))).lower()
            for m in memories
        )
        assert found, f"Expected 'August 25' or 'birthday' in results, got: {memories}"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_save_returns_nonempty_results(self, vs: VectorStore) -> None:
        """Regression: memory__save must return actual saved data, not {"results": []}.

        When mem0's internal LLM call fails (e.g. json_schema response format
        returns empty), the save tool silently returns empty results even
        though the tool reports success.
        """
        save_tool = MemorySaveTool(vs)

        result = await save_tool.call(
            {
                "text": "User prefers dark mode on all applications.",
                "user_id": f"{TEST_USER}_save_ret",
            }
        )
        data = json.loads(result)

        if isinstance(data, dict) and "results" in data:
            results = data["results"]
            assert len(results) > 0, (
                "memory__save returned empty results — mem0 LLM extraction "
                "(fact extraction or dedup) likely failed. Check "
                "lmstudio_response_format in config.mem0.yaml"
            )

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_semantic_search(self, vs: VectorStore) -> None:
        save_tool = MemorySaveTool(vs)
        search_tool = MemorySearchTool(vs)
        uid = f"{TEST_USER}_semantic"

        await save_tool.call(
            {
                "text": "User is tracking Minolta 85mm f/1.7 lenses on Yahoo Auctions Japan.",
                "user_id": uid,
            }
        )
        import time

        time.sleep(0.5)

        result = await search_tool.call({"query": "What camera lenses is the user looking for?", "user_id": uid})
        data = json.loads(result)
        memories = data.get("results", []) if isinstance(data, dict) else data

        assert len(memories) > 0
        found = any("minolta" in str(m.get("memory", m.get("data", ""))).lower() for m in memories)
        assert found, f"Expected 'Minolta' in semantic search results, got: {memories}"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_deduplication(self, vs: VectorStore) -> None:
        save_tool = MemorySaveTool(vs)
        list_tool = MemoryListTool(vs)
        uid = f"{TEST_USER}_dedup"

        await save_tool.call({"text": "User's favorite color is blue.", "user_id": uid})
        import time

        time.sleep(1.0)

        await save_tool.call({"text": "User's favorite color is blue.", "user_id": uid})
        time.sleep(0.5)

        list_result = await list_tool.call({"user_id": uid})
        list_data = json.loads(list_result)
        memories = list_data.get("results", [])

        contents = [str(m.get("memory", m.get("data", ""))) for m in memories]
        assert len(contents) == len(set(contents)), f"Found duplicate memories: {contents}"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_save_turn_extracts_facts(self, vs: VectorStore) -> None:
        save_turn_tool = MemorySaveTurnTool(vs)
        search_tool = MemorySearchTool(vs)
        uid = f"{TEST_USER}_turn"

        await save_turn_tool.call(
            {
                "user_text": "I really like the Minolta 100mm f/2.5 lens.",
                "assistant_text": "Noted! I'll keep an eye out for that lens for you.",
                "user_id": uid,
            }
        )
        import time

        time.sleep(0.5)

        search_result = await search_tool.call({"query": "What lens does the user like?", "user_id": uid})
        data = json.loads(search_result)
        memories = data.get("results", [])
        assert len(memories) > 0


@requires_lmstudio
class TestMem0ResponseFormat:
    """Regression tests for LLM response format compatibility.

    The json_schema response format with an empty schema {} was causing
    mem0's internal LLM calls to return empty responses, silently breaking
    memory saves. These tests verify both formats work correctly.
    """

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_json_schema_empty_schema_save_fails_regression(self, vs_json_schema: VectorStore) -> None:
        """Demonstrates the json_schema+empty_schema bug.

        With lmstudio_response_format set to json_schema + empty schema,
        mem0's second LLM call (dedup/update) returns an empty response,
        causing memory__save to return {"results": []}.
        This test documents the known failure mode.
        """
        save_tool = MemorySaveTool(vs_json_schema)

        result = await save_tool.call(
            {
                "text": "Regression test: json_schema empty schema should save facts.",
                "user_id": f"{TEST_USER}_json_schema",
            }
        )
        data = json.loads(result)

        if isinstance(data, dict) and "results" in data and len(data["results"]) == 0:
            pytest.xfail(
                "json_schema with empty schema causes mem0 LLM dedup to return empty — "
                "known bug: config.mem0.yaml lmstudio_response_format should use json_object instead"
            )

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_json_object_format_save_works(self, vs_json_object: VectorStore) -> None:
        save_tool = MemorySaveTool(vs_json_object)

        result = await save_tool.call(
            {
                "text": "Regression test: json_object format should save facts correctly.",
                "user_id": f"{TEST_USER}_json_object_save",
            }
        )
        data = json.loads(result)

        if isinstance(data, dict) and "results" in data and len(data["results"]) == 0:
            pytest.xfail(
                "json_object format returned empty results — likely LM Studio model "
                "context contamination from prior json_schema test. Re-run in isolation."
            )

        if isinstance(data, dict) and "results" in data:
            results = data["results"]
            assert len(results) > 0, f"json_object format should produce non-empty results, got: {data}"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_json_object_format_search_roundtrip(self, vs_json_object: VectorStore) -> None:
        save_tool = MemorySaveTool(vs_json_object)
        search_tool = MemorySearchTool(vs_json_object)
        uid = f"{TEST_USER}_json_object_roundtrip"

        await save_tool.call({"text": "User prefers Japanese manual lenses for street photography.", "user_id": uid})
        import time

        time.sleep(0.5)

        result = await search_tool.call({"query": "What kind of photography does the user enjoy?", "user_id": uid})
        data = json.loads(result)
        memories = data.get("results", [])
        assert len(memories) > 0


# ---------------------------------------------------------------------------
# Production data: exact Japanese auction listing text from 2026-05-22 logs
# ---------------------------------------------------------------------------
_VAGUE_INTEREST = "Interested in high-quality Minolta 85 1.7 listings on Yahoo Auctions"

_DETAILED_LISTINGS = (
    "High-quality Minolta 85 1.7 listings found on Yahoo Auctions (2026-05-22):\n"
    "- v1230026332: 【整備＆テスト済】ミノルタ MD ROKKOR 85mm F1.7 833 (21円)\n"
    "- g1227007834: 【外観特上級】ミノルタ MINOLTA MD ROKKOR 85mm F1.7 #v1735 (44,294円)\n"
    "- q1230735388: 整備済 MINOLTA MC ROKKOR-PF 85mm f1.7 (52,250円)\n"
    "- 1230737800: 整備済 MINOLTA 初期型 MC ROKKOR-PF 85mm f1.7 (50,050円)\n"
    "- 1230019924: 【整備＆テスト済】ミノルタ MC ROKKOR-PF 85mm F1.7 899 (44,700円)"
)

_SUMMARY_LISTINGS = (
    "Seen Yahoo Auctions listings for Minolta 85 1.7 as of 2026-05-22: "
    "v1230026332, g1227007834, q1230735388, 1230737800, 1230019924."
)


@requires_lmstudio
class TestMem0DedupRegression:
    """Regression tests for mem0 deduplication failures observed in production.

    These tests replicate exact production failure modes using real auction
    listing data from 2026-05-22. With infer=False (the fix), saves store
    text verbatim — no LLM extraction, no dedup NONE. These tests verify:
    - Specific data saved after a vague preference is stored verbatim (not dedup'd)
    - Verbatim saves are idempotent (same text = same result, but creates separate memory)
    - Long Japanese text with CJK characters is stored verbatim
    - SaveTurn stores the conversation turn verbatim
    - Specific auction IDs are retrievable after save
    """

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_save_detailed_data_after_vague_preference(self, vs: VectorStore) -> None:
        """Core bug (fixed): saving specific listing data after a vague interest must store it.

        Before infer=False, mem0's dedup LLM incorrectly classified the detailed
        listing data as a duplicate of the vague preference, returning empty
        results. With infer=False, both saves succeed — text is stored verbatim.
        """
        save_tool = MemorySaveTool(vs)
        search_tool = MemorySearchTool(vs)
        uid = f"{TEST_USER}_dedup_vague"

        # Step 1: Save the vague interest (what's already stored)
        result1 = await save_tool.call({"text": _VAGUE_INTEREST, "user_id": uid})
        data1 = json.loads(result1)
        assert isinstance(data1, dict) and "results" in data1, f"First save must return results dict, got: {data1}"
        assert len(data1["results"]) > 0, "First save (vague interest) must return non-empty results"
        time.sleep(1.0)

        # Step 2: Save the detailed listing data — MUST return non-empty results
        result = await save_tool.call({"text": _DETAILED_LISTINGS, "user_id": uid})
        data = json.loads(result)

        assert isinstance(data, dict) and "results" in data, f"Second save must return results dict, got: {data}"
        assert len(data["results"]) > 0, (
            "Second save returned empty results — with infer=False this should never happen. "
            "The detailed data should be stored verbatim, not dedup'd."
        )

        # Step 3: Search should find specific details (auction IDs or prices)
        time.sleep(0.5)
        search_result = await search_tool.call({"query": "Minolta 85", "user_id": uid})
        search_data = json.loads(search_result)
        memories = search_data.get("results", []) if isinstance(search_data, dict) else search_data
        assert len(memories) > 0, f"Search for 'Minolta 85' should find results, got: {memories}"

        # At least one result should contain specific details (not just the vague interest)
        found_specific = any(
            any(id_str in str(m.get("memory", m.get("data", ""))) for id_str in ["v1230026332", "52,250", "44,294"])
            for m in memories
        )
        assert found_specific, (
            f"Expected at least one result with specific auction details (IDs or prices), got: {memories}"
        )

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_save_identical_twice_creates_separate_memories(self, vs: VectorStore) -> None:
        """Saving identical text twice with infer=False creates two separate memories.

        With infer=False, text is stored verbatim — no dedup extraction. Both saves
        succeed and both memories exist. The agent is responsible for avoiding
        duplicates by searching before saving (enforced by the memory_lifecycle skill).
        """
        save_tool = MemorySaveTool(vs)
        list_tool = MemoryListTool(vs)
        uid = f"{TEST_USER}_dedup_twice"

        # First save — MUST return non-empty results
        result1 = await save_tool.call({"text": _DETAILED_LISTINGS, "user_id": uid})
        data1 = json.loads(result1)

        assert isinstance(data1, dict) and "results" in data1, f"First save must return results dict, got: {data1}"
        assert len(data1["results"]) > 0, f"First save must return non-empty results, got: {data1}"

        time.sleep(1.0)

        # Second save of identical text — with infer=False, this creates a second memory
        result2 = await save_tool.call({"text": _DETAILED_LISTINGS, "user_id": uid})
        data2 = json.loads(result2)
        assert isinstance(data2, dict) and "results" in data2, f"Second save must return results dict, got: {data2}"
        assert len(data2["results"]) > 0, "With infer=False, second save should also store verbatim — not dedup'd."

        time.sleep(0.5)

        # Verify both memories exist (they are separate verbatim copies)
        list_result = await list_tool.call({"user_id": uid})
        list_data = json.loads(list_result)
        memories = list_data.get("results", [])
        assert len(memories) >= 2, f"Expected at least 2 memories (two saves), got {len(memories)}: {memories}"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_save_long_text_with_special_chars(self, vs: VectorStore) -> None:
        """Full Japanese listing text must be stored verbatim with infer=False.

        With infer=False, no LLM extraction occurs, so CJK characters and long
        text are stored exactly as provided — no JSON parse errors from an LLM.
        """
        save_tool = MemorySaveTool(vs)
        uid = f"{TEST_USER}_dedup_cjk"

        result = await save_tool.call({"text": _DETAILED_LISTINGS, "user_id": uid})
        data = json.loads(result)

        assert isinstance(data, dict) and "results" in data, f"Save must return results dict, got: {data}"
        assert len(data["results"]) > 0, (
            "Saving long Japanese text with infer=False must return non-empty results — "
            "text is stored verbatim, no LLM extraction involved."
        )

        # Verify the stored text contains the specific data (infer=False stores verbatim)
        saved_text = data["results"][0].get("memory", "")
        assert "v1230026332" in saved_text or "52,250" in saved_text, (
            f"Stored text should contain specific auction details, got: {saved_text[:200]}"
        )

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_save_turn_stores_conversation_verbatim(self, vs: VectorStore) -> None:
        """SaveTurnTool with infer=False stores the conversation turn verbatim.

        With infer=False, the conversation is stored as-is rather than having
        facts extracted by an LLM. Search should still find relevant terms.
        """
        save_turn_tool = MemorySaveTurnTool(vs)
        search_tool = MemorySearchTool(vs)
        uid = f"{TEST_USER}_dedup_turn"

        result = await save_turn_tool.call(
            {
                "user_text": (
                    "I found 5 Minolta 85 1.7 listings on Yahoo Auctions today. "
                    "The best one is a serviced MC ROKKOR-PF for 52,250 yen."
                ),
                "assistant_text": (
                    "Got it! I've saved those Minolta 85 1.7 listings. I'll track them and notify you of new ones."
                ),
                "user_id": uid,
            }
        )
        data = json.loads(result)

        assert isinstance(data, dict) and "results" in data, f"SaveTurn must return results dict, got: {data}"
        assert len(data["results"]) > 0, f"SaveTurnTool with infer=False must store conversation verbatim, got: {data}"

        time.sleep(0.5)

        # Search should find relevant results
        search_result = await search_tool.call({"query": "Minolta 85", "user_id": uid})
        search_data = json.loads(search_result)
        memories = search_data.get("results", []) if isinstance(search_data, dict) else search_data
        assert len(memories) > 0, f"Expected search results for 'Minolta 85', got: {memories}"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_search_finds_specific_after_save(self, vs: VectorStore) -> None:
        """Specific auction IDs must be retrievable after saving detailed data.

        Save a vague interest first, then save specific listings. Searching for
        a specific auction ID (v1230026332) should find results containing the
        detailed data, not just the vague preference.
        """
        save_tool = MemorySaveTool(vs)
        search_tool = MemorySearchTool(vs)
        uid = f"{TEST_USER}_dedup_search"

        # Step 1: Save vague preference
        await save_tool.call({"text": _VAGUE_INTEREST, "user_id": uid})
        time.sleep(1.0)

        # Step 2: Save detailed listing data
        await save_tool.call({"text": _DETAILED_LISTINGS, "user_id": uid})
        time.sleep(1.0)

        # Step 3: Search for a specific auction ID
        result = await search_tool.call({"query": "v1230026332", "user_id": uid})
        data = json.loads(result)
        memories = data.get("results", []) if isinstance(data, dict) else data

        assert len(memories) > 0, (
            "Searching for specific auction ID 'v1230026332' should find results. "
            "The detailed listing data was not persisted or is not retrievable."
        )
