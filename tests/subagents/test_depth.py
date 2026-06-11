"""Tests for subagent depth enforcement in SubagentManager.

See issue #43.
"""

from __future__ import annotations

from pathlib import Path
from typing import cast

import pytest

from nanobot.context_store import ContextStore
from nanobot.skills.store import SkillStore
from nanobot.subagents.manager import MAX_SUBAGENT_DEPTH, SubagentManager
from nanobot.subagents.store import SubagentRunStore


def _make_manager(tmp_path: Path) -> SubagentManager:
    """Build a SubagentManager with only the dependencies spawn() actually uses.

    Constructor args agent_run, tools, prompts, mem0_store are unused by
    _compute_depth, the depth check, and the post-check spawn() body, so None
    placeholders are fine for these targeted tests. skills needs a real
    SkillStore because spawn() calls self._skill_matcher.find_relevant_skills()
    on the goal; contexts needs a real ContextStore because spawn() writes
    the goal and status to it.
    """
    db_path = str(tmp_path / "nanobot.db")
    skill_db_path = str(tmp_path / "skills.db")
    SubagentRunStore(db_path)  # creates schema; manager below will share the file
    contexts = ContextStore(db_path)
    skills = SkillStore(skill_db_path)
    manager = SubagentManager(
        db_path=db_path,
        contexts=contexts,
        agent_run=cast(object, None),
        tools=cast(object, None),
        skills=skills,
        prompts=cast(object, None),
        mem0_store=cast(object, None),
    )
    return manager


class TestComputeDepth:
    def test_no_parent_returns_depth_zero(self, tmp_path: Path) -> None:
        """_compute_depth(None) = 0: a new run with no parent sits at the root."""
        manager = _make_manager(tmp_path)
        assert manager._compute_depth(None) == 0

    def test_parent_with_no_parent_returns_depth_one(self, tmp_path: Path) -> None:
        """_compute_depth(<orchestrator>) = 1: a new run with the orchestrator as parent is the first child."""
        manager = _make_manager(tmp_path)
        orchestrator = manager._store.create(run_id="run-orchestrator", scope="test:1", parent_run_id=None)
        assert orchestrator.parent_run_id is None
        assert manager._compute_depth(orchestrator.id) == 1

    def test_grandparent_chain_returns_depth_two(self, tmp_path: Path) -> None:
        """_compute_depth(<child>) = 2: a new run with a child as parent is a grandchild (would be blocked)."""
        manager = _make_manager(tmp_path)
        orchestrator = manager._store.create(run_id="run-orchestrator", scope="test:1", parent_run_id=None)
        child = manager._store.create(run_id="run-child", scope="test:1", parent_run_id=orchestrator.id)
        assert manager._compute_depth(child.id) == 2

    def test_deep_chain_walks_fully(self, tmp_path: Path) -> None:
        """A 5-node chain (r0 -> r1 -> r2 -> r3 -> r4) gives _compute_depth(r4) = 5.

        A new run with parent=r4 would be the 6th node in the chain, but the
        function returns the count of nodes from r4 up to (and including) the
        parentless root. That count is 5 for this chain. The point of the
        test is that the walk doesn't stop early on a deep chain.
        """
        manager = _make_manager(tmp_path)
        # Build: r0 -> r1 -> r2 -> r3 -> r4
        r0 = manager._store.create(run_id="run-r0", scope="t", parent_run_id=None)
        r1 = manager._store.create(run_id="run-r1", scope="t", parent_run_id=r0.id)
        r2 = manager._store.create(run_id="run-r2", scope="t", parent_run_id=r1.id)
        r3 = manager._store.create(run_id="run-r3", scope="t", parent_run_id=r2.id)
        r4 = manager._store.create(run_id="run-r4", scope="t", parent_run_id=r3.id)
        assert manager._compute_depth(r4.id) == 5

    def test_missing_parent_treats_as_orphan(self, tmp_path: Path) -> None:
        """Defensive: a missing parent_run_id is treated as depth 0 (orphan), not an error.

        Prevents infinite loops if a parent row is deleted while a child is
        being spawned.
        """
        manager = _make_manager(tmp_path)
        assert manager._compute_depth("run-does-not-exist") == 0


class TestSpawnDepthCheck:
    def test_spawn_with_no_parent_succeeds(self, tmp_path: Path) -> None:
        """A new run with no parent (orchestrator or scheduled) is allowed at depth 0."""
        manager = _make_manager(tmp_path)
        run = manager.spawn(scope="test:1", goal="root task")
        assert run.parent_run_id is None
        # The depth that this run would offer to its own child is 1.
        assert manager._compute_depth(run.id) == 1

    def test_spawn_with_orchestrator_parent_succeeds(self, tmp_path: Path) -> None:
        """A new run with an orchestrator as parent is allowed at depth 1 (first child)."""
        manager = _make_manager(tmp_path)
        orchestrator = manager.spawn(scope="test:1", goal="orchestrator task")
        # The delegate_task tool will call spawn with parent_run_id=orchestrator.id.
        child = manager.spawn(scope="test:1", parent_run_id=orchestrator.id, goal="child task")
        assert child.parent_run_id == orchestrator.id
        # The depth that this child would offer to its own child is 2 (grandchild — blocked).
        assert manager._compute_depth(child.id) == 2

    def test_spawn_with_child_parent_raises_value_error(self, tmp_path: Path) -> None:
        """A new run whose parent is itself a child (i.e. depth 2) is refused by spawn."""
        manager = _make_manager(tmp_path)
        orchestrator = manager.spawn(scope="test:1", goal="orchestrator task")
        child = manager.spawn(scope="test:1", parent_run_id=orchestrator.id, goal="child task")
        with pytest.raises(ValueError, match="depth 2 > MAX_SUBAGENT_DEPTH"):
            manager.spawn(scope="test:1", parent_run_id=child.id, goal="grandchild task")

    def test_max_depth_constant_is_one(self) -> None:
        """Sanity: the constant matches the design decision (depth 0 = orchestrator, 1 = max child)."""
        assert MAX_SUBAGENT_DEPTH == 1
