from __future__ import annotations

import tempfile
from pathlib import Path

from nanobot.plans import Plan, PlanBrief, PlanStore


def _make_store(tmp_path: Path) -> PlanStore:
    db_path = str(tmp_path / "plans.db")
    return PlanStore(db_path)


def test_create_plan_basic() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        store = _make_store(Path(tmpdir))
        plan = store.create(name="Test Plan", goal="Do something")

        assert plan.id == 1
        assert plan.name == "Test Plan"
        assert plan.goal == "Do something"
        assert plan.constraints == []
        assert plan.required_inputs == []
        assert plan.risk_flags == []
        assert plan.steps is None
        assert plan.notes == ""
        assert plan.source_type == ""
        assert plan.source_scope == ""
        assert plan.success_count == 0
        assert plan.failure_count == 0


def test_create_plan_with_all_fields() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        store = _make_store(Path(tmpdir))
        plan = store.create(
            name="Full Plan",
            goal="Complete task",
            constraints=["budget under $100", "finish by Friday"],
            required_inputs=["API key", "credentials"],
            risk_flags=["rate limits", "downtime"],
            steps=[{"action": "fetch", "args": {"url": "example.com"}}],
            notes="Important task",
            source_type="plan_command",
            source_scope="telegram:123456",
        )

        assert plan.id == 1
        assert plan.name == "Full Plan"
        assert plan.goal == "Complete task"
        assert plan.constraints == ["budget under $100", "finish by Friday"]
        assert plan.required_inputs == ["API key", "credentials"]
        assert plan.risk_flags == ["rate limits", "downtime"]
        assert plan.steps == [{"action": "fetch", "args": {"url": "example.com"}}]
        assert plan.notes == "Important task"
        assert plan.source_type == "plan_command"
        assert plan.source_scope == "telegram:123456"


def test_create_from_brief() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        store = _make_store(Path(tmpdir))
        brief = PlanBrief(
            goal="Buy groceries",
            constraints=["under $50"],
            required_inputs=["shopping list"],
            risk_flags=["store closed"],
            notes="Weekly task",
        )
        plan = store.create_from_brief(
            brief=brief,
            name="Grocery Plan",
            source_type="plan_command",
            source_scope="telegram:999",
        )

        assert plan.id == 1
        assert plan.goal == "Buy groceries"
        assert plan.constraints == ["under $50"]
        assert plan.required_inputs == ["shopping list"]
        assert plan.risk_flags == ["store closed"]
        assert plan.notes == "Weekly task"


def test_get_plan() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        store = _make_store(Path(tmpdir))
        created = store.create(name="Get Test", goal="Retrieve me")
        fetched = store.get(created.id)

        assert fetched is not None
        assert fetched.id == created.id
        assert fetched.name == "Get Test"
        assert fetched.goal == "Retrieve me"


def test_get_nonexistent_plan() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        store = _make_store(Path(tmpdir))
        fetched = store.get(999)
        assert fetched is None


def test_list_plans() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        store = _make_store(Path(tmpdir))
        store.create(name="Plan A", goal="Goal A")
        store.create(name="Plan B", goal="Goal B")
        store.create(name="Plan C", goal="Goal C")

        plans = store.list_plans()
        assert len(plans) == 3
        assert plans[0].name == "Plan C"
        assert plans[1].name == "Plan B"
        assert plans[2].name == "Plan A"


def test_list_plans_filter_by_source() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        store = _make_store(Path(tmpdir))
        store.create(name="P1", goal="G1", source_type="plan_command", source_scope="telegram:1")
        store.create(name="P2", goal="G2", source_type="scheduled_task", source_scope="telegram:2")
        store.create(name="P3", goal="G3", source_type="plan_command", source_scope="telegram:3")

        command_plans = store.list_plans(source_type="plan_command")
        assert len(command_plans) == 2
        assert all(p.source_type == "plan_command" for p in command_plans)

        scoped_plans = store.list_plans(source_scope="telegram:2")
        assert len(scoped_plans) == 1
        assert scoped_plans[0].name == "P2"


def test_list_plans_limit() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        store = _make_store(Path(tmpdir))
        for i in range(10):
            store.create(name=f"Plan {i}", goal=f"Goal {i}")

        plans = store.list_plans(limit=5)
        assert len(plans) == 5


def test_update_plan() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        store = _make_store(Path(tmpdir))
        created = store.create(name="Old Name", goal="Old Goal")

        updated = store.update(
            created.id,
            name="New Name",
            goal="New Goal",
            constraints=["new constraint"],
        )

        assert updated is not None
        assert updated.name == "New Name"
        assert updated.goal == "New Goal"
        assert updated.constraints == ["new constraint"]


def test_update_plan_increment_version() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        store = _make_store(Path(tmpdir))
        created = store.create(name="Test", goal="Test")

        updated = store.update(created.id, name="Updated", increment_version=True)
        assert updated is not None
        assert updated.version == 2


def test_update_nonexistent_plan() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        store = _make_store(Path(tmpdir))
        updated = store.update(999, name="Doesn't matter")
        assert updated is None


def test_increment_stats_success() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        store = _make_store(Path(tmpdir))
        created = store.create(name="Test", goal="Test")

        updated = store.increment_stats(created.id, success=True)
        assert updated is not None
        assert updated.success_count == 1
        assert updated.failure_count == 0
        assert updated.last_run_at is not None


def test_increment_stats_failure() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        store = _make_store(Path(tmpdir))
        created = store.create(name="Test", goal="Test")

        updated = store.increment_stats(created.id, success=False)
        assert updated is not None
        assert updated.success_count == 0
        assert updated.failure_count == 1


def test_delete_plan() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        store = _make_store(Path(tmpdir))
        created = store.create(name="Delete Me", goal="Soon")

        deleted = store.delete(created.id)
        assert deleted is True

        fetched = store.get(created.id)
        assert fetched is None


def test_delete_nonexistent_plan() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        store = _make_store(Path(tmpdir))
        deleted = store.delete(999)
        assert deleted is False


def test_plan_as_dict() -> None:
    plan = Plan(
        id=1,
        name="Test",
        goal="Do something",
        constraints=["c1"],
        required_inputs=["i1"],
        risk_flags=["r1"],
        notes="notes",
        source_type="plan_command",
        source_scope="telegram:123",
        version=1,
        success_count=5,
        failure_count=2,
    )
    result = plan.as_dict()

    assert result["id"] == 1
    assert result["name"] == "Test"
    assert result["goal"] == "Do something"
    assert result["constraints"] == ["c1"]
    assert result["required_inputs"] == ["i1"]
    assert result["risk_flags"] == ["r1"]
    assert result["notes"] == "notes"
    assert result["source_type"] == "plan_command"
    assert result["source_scope"] == "telegram:123"
    assert result["version"] == 1
    assert result["success_count"] == 5
    assert result["failure_count"] == 2
    assert "created_at" in result
    assert "updated_at" in result


def test_plan_brief_from_dict() -> None:
    data = {
        "goal": "Test goal",
        "constraints": ["c1", "c2"],
        "required_inputs": ["i1"],
        "risk_flags": ["r1"],
        "notes": "test notes",
    }
    brief = PlanBrief.from_dict(data)

    assert brief.goal == "Test goal"
    assert brief.constraints == ["c1", "c2"]
    assert brief.required_inputs == ["i1"]
    assert brief.risk_flags == ["r1"]
    assert brief.notes == "test notes"


def test_plan_brief_as_dict() -> None:
    brief = PlanBrief(
        goal="Goal",
        constraints=["c1"],
        required_inputs=["i1"],
        risk_flags=["r1"],
        notes="notes",
    )
    result = brief.as_dict()

    assert result["goal"] == "Goal"
    assert result["constraints"] == ["c1"]
    assert result["required_inputs"] == ["i1"]
    assert result["risk_flags"] == ["r1"]
    assert result["notes"] == "notes"
