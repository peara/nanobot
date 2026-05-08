from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from nanobot.scripts.models import ScriptVersionRecord, SearchCandidate
from nanobot.scripts.ranker import rank_script_candidate
from nanobot.scripts.vector_store import ScriptVectorStore
from nanobot.storage.sqlite import NanoScriptSqlite


class ScriptRegistry:
    def __init__(self, db_path: str) -> None:
        self.sqlite = NanoScriptSqlite(db_path)
        self.vector_store = ScriptVectorStore(self.sqlite)

    def create_script(
        self,
        *,
        name: str,
        description: str,
        domain: str | None,
        task_type: str | None,
        code: str,
        params_schema: dict[str, Any],
        output_schema: dict[str, Any],
        selector_manifest: dict[str, list[str]],
        validation_rules: list[dict[str, Any]] | None,
        embedding_text: str,
        created_by: str,
    ) -> tuple[str, str]:
        script_id = self._id("scr")
        version_id = self._id("ver")
        now = self._now()

        with self.sqlite.connect() as conn:
            conn.execute(
                """
                INSERT INTO scripts(
                    id, name, description, domain, task_type,
                    current_version_id, status, created_at, updated_at
                )
                VALUES(?, ?, ?, ?, ?, ?, 'active', ?, ?)
                """,
                (script_id, name, description, domain, task_type, version_id, now, now),
            )
            conn.execute(
                """
                INSERT INTO script_versions(
                    id, script_id, version, code, params_schema, output_schema,
                    selector_manifest, validation_rules, changelog, status, created_by, created_at
                ) VALUES (?, ?, 1, ?, ?, ?, ?, ?, ?, 'active', ?, ?)
                """,
                (
                    version_id,
                    script_id,
                    code,
                    json.dumps(params_schema, ensure_ascii=True),
                    json.dumps(output_schema, ensure_ascii=True),
                    json.dumps(selector_manifest, ensure_ascii=True),
                    json.dumps(validation_rules or [], ensure_ascii=True),
                    "initial",
                    created_by,
                    now,
                ),
            )

        self.vector_store.upsert(script_id, embedding_text)
        return script_id, version_id

    def get_script_version(self, script_id: str, version_id: str | None = None) -> ScriptVersionRecord | None:
        with self.sqlite.connect() as conn:
            conn.row_factory = None
            if version_id:
                row = conn.execute(
                    """
                    SELECT s.id, s.name, s.description, s.domain, s.task_type, s.current_version_id,
                           v.id, v.version, v.code, v.params_schema, v.output_schema,
                           v.selector_manifest, v.validation_rules, v.status
                    FROM scripts s
                    JOIN script_versions v ON v.script_id = s.id
                    WHERE s.id = ? AND v.id = ?
                    """,
                    (script_id, version_id),
                ).fetchone()
            else:
                row = conn.execute(
                    """
                    SELECT s.id, s.name, s.description, s.domain, s.task_type, s.current_version_id,
                           v.id, v.version, v.code, v.params_schema, v.output_schema,
                           v.selector_manifest, v.validation_rules, v.status
                    FROM scripts s
                    JOIN script_versions v ON v.id = s.current_version_id
                    WHERE s.id = ?
                    """,
                    (script_id,),
                ).fetchone()

        if row is None:
            return None

        return ScriptVersionRecord(
            script_id=str(row[0]),
            script_name=str(row[1]),
            description=str(row[2]),
            domain=row[3],
            task_type=row[4],
            current_version_id=row[5],
            version_id=str(row[6]),
            version=int(row[7]),
            code=str(row[8]),
            params_schema=json.loads(row[9]),
            output_schema=json.loads(row[10]),
            selector_manifest=json.loads(row[11]),
            validation_rules=json.loads(row[12]) if row[12] else [],
            status=str(row[13]),
        )

    def create_candidate_version(
        self,
        script_id: str,
        *,
        code: str,
        params_schema: dict[str, Any],
        output_schema: dict[str, Any],
        selector_manifest: dict[str, list[str]],
        validation_rules: list[dict[str, Any]] | None,
        changelog: str,
        created_by: str,
    ) -> str:
        version_id = self._id("ver")
        now = self._now()
        with self.sqlite.connect() as conn:
            current = conn.execute(
                "SELECT MAX(version) FROM script_versions WHERE script_id = ?",
                (script_id,),
            ).fetchone()
            next_version = int(current[0] or 0) + 1
            conn.execute(
                """
                INSERT INTO script_versions(
                    id, script_id, version, code, params_schema, output_schema,
                    selector_manifest, validation_rules, changelog, status, created_by, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'candidate', ?, ?)
                """,
                (
                    version_id,
                    script_id,
                    next_version,
                    code,
                    json.dumps(params_schema, ensure_ascii=True),
                    json.dumps(output_schema, ensure_ascii=True),
                    json.dumps(selector_manifest, ensure_ascii=True),
                    json.dumps(validation_rules or [], ensure_ascii=True),
                    changelog,
                    created_by,
                    now,
                ),
            )
            conn.execute("UPDATE scripts SET updated_at = ? WHERE id = ?", (now, script_id))
        return version_id

    def promote_version(self, script_id: str, version_id: str) -> None:
        now = self._now()
        with self.sqlite.connect() as conn:
            previous = conn.execute("SELECT current_version_id FROM scripts WHERE id = ?", (script_id,)).fetchone()
            if previous and previous[0]:
                conn.execute(
                    "UPDATE script_versions SET status = 'archived' WHERE id = ?",
                    (str(previous[0]),),
                )
            conn.execute(
                "UPDATE script_versions SET status = 'active' WHERE id = ?",
                (version_id,),
            )
            conn.execute(
                "UPDATE scripts SET current_version_id = ?, updated_at = ? WHERE id = ?",
                (version_id, now, script_id),
            )

    def mark_version_failed(self, version_id: str, status: str = "failed") -> None:
        with self.sqlite.connect() as conn:
            conn.execute("UPDATE script_versions SET status = ? WHERE id = ?", (status, version_id))

    def rollback_to_best(self, script_id: str) -> str | None:
        with self.sqlite.connect() as conn:
            rows = conn.execute(
                """
                SELECT v.id,
                       COALESCE(SUM(CASE WHEN e.status = 'success' THEN 1 ELSE 0 END), 0) AS succ,
                       COALESCE(SUM(CASE WHEN e.status = 'failed' THEN 1 ELSE 0 END), 0) AS fail
                FROM script_versions v
                LEFT JOIN executions e ON e.version_id = v.id
                WHERE v.script_id = ? AND v.status IN ('active', 'archived')
                GROUP BY v.id
                ORDER BY (1.0 * succ / NULLIF(succ + fail, 0)) DESC, succ DESC
                """,
                (script_id,),
            ).fetchall()
            if not rows:
                return None
            version_id = str(rows[0][0])
        self.promote_version(script_id, version_id)
        return version_id

    def update_selector_stat(self, script_id: str, selector_key: str, selector: str, success: bool) -> None:
        now = self._now()
        with self.sqlite.connect() as conn:
            existing = conn.execute(
                "SELECT id FROM selector_stats WHERE script_id = ? AND selector_key = ? AND selector = ?",
                (script_id, selector_key, selector),
            ).fetchone()
            if existing is None:
                stat_id = self._id("sel")
                conn.execute(
                    """
                    INSERT INTO selector_stats(
                        id, script_id, selector_key, selector, success_count,
                        failure_count, last_success_at, last_failure_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        stat_id,
                        script_id,
                        selector_key,
                        selector,
                        1 if success else 0,
                        0 if success else 1,
                        now if success else None,
                        None if success else now,
                    ),
                )
                return

            if success:
                conn.execute(
                    """
                    UPDATE selector_stats
                    SET success_count = success_count + 1, last_success_at = ?
                    WHERE script_id = ? AND selector_key = ? AND selector = ?
                    """,
                    (now, script_id, selector_key, selector),
                )
            else:
                conn.execute(
                    """
                    UPDATE selector_stats
                    SET failure_count = failure_count + 1, last_failure_at = ?
                    WHERE script_id = ? AND selector_key = ? AND selector = ?
                    """,
                    (now, script_id, selector_key, selector),
                )

    def create_execution(
        self,
        *,
        script_id: str,
        version_id: str,
        params: dict[str, Any],
        status: str,
        result: dict[str, Any] | None,
        error_type: str | None,
        error_message: str | None,
        duration_ms: int,
        dom_query_count: int,
        page_count: int,
        click_count: int,
        output_item_count: int,
        confidence: float,
    ) -> str:
        execution_id = self._id("exe")
        now = self._now()
        with self.sqlite.connect() as conn:
            conn.execute(
                """
                INSERT INTO executions(
                    id, script_id, version_id, params, result, status, error_type, error_message,
                    duration_ms, dom_query_count, page_count, click_count, output_item_count,
                    confidence, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    execution_id,
                    script_id,
                    version_id,
                    json.dumps(params, ensure_ascii=True),
                    json.dumps(result, ensure_ascii=True) if result is not None else None,
                    status,
                    error_type,
                    error_message,
                    duration_ms,
                    dom_query_count,
                    page_count,
                    click_count,
                    output_item_count,
                    confidence,
                    now,
                ),
            )
        return execution_id

    def save_execution_traces(self, execution_id: str, traces: list[dict[str, Any]]) -> None:
        now = self._now()
        with self.sqlite.connect() as conn:
            for index, trace in enumerate(traces):
                conn.execute(
                    """
                    INSERT INTO execution_traces(
                        id, execution_id, step_index, action, selector_key, selector_used,
                        url, status, error, snapshot_ref, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        self._id("trc"),
                        execution_id,
                        int(trace.get("step_index", index + 1)),
                        str(trace.get("action", "unknown")),
                        trace.get("selector_key"),
                        trace.get("selector_used"),
                        trace.get("url"),
                        trace.get("status"),
                        trace.get("error"),
                        trace.get("snapshot_ref"),
                        now,
                    ),
                )

    def get_execution(self, execution_id: str) -> dict[str, Any] | None:
        with self.sqlite.connect() as conn:
            row = conn.execute(
                """
                SELECT id, script_id, version_id, params, result, status, error_type, error_message,
                       duration_ms, confidence, created_at
                FROM executions WHERE id = ?
                """,
                (execution_id,),
            ).fetchone()
            if row is None:
                return None
        return {
            "id": row[0],
            "script_id": row[1],
            "version_id": row[2],
            "params": json.loads(row[3]),
            "result": json.loads(row[4]) if row[4] else None,
            "status": row[5],
            "error_type": row[6],
            "error_message": row[7],
            "duration_ms": row[8],
            "confidence": row[9],
            "created_at": row[10],
        }

    def script_success_rate(self, script_id: str) -> float:
        with self.sqlite.connect() as conn:
            row = conn.execute(
                """
                SELECT
                  COALESCE(SUM(CASE WHEN status = 'success' THEN 1 ELSE 0 END), 0),
                  COUNT(*)
                FROM executions
                WHERE script_id = ?
                """,
                (script_id,),
            ).fetchone()
        success = int(row[0] or 0)
        total = int(row[1] or 0)
        if total == 0:
            return 0.5
        return success / total

    def recent_failure_rate(self, script_id: str, window: int = 5) -> float:
        with self.sqlite.connect() as conn:
            rows = conn.execute(
                """
                SELECT status
                FROM executions
                WHERE script_id = ?
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (script_id, window),
            ).fetchall()
        if not rows:
            return 0.0
        failures = sum(1 for row in rows if row[0] == "failed")
        return failures / len(rows)

    def search_scripts(self, query: str, params: dict[str, Any] | None, limit: int = 5) -> list[SearchCandidate]:
        raw = self.vector_store.search(query, limit=max(10, limit * 2))
        ranked: list[SearchCandidate] = []
        with self.sqlite.connect() as conn:
            for item in raw:
                row = conn.execute(
                    """
                    SELECT s.id, s.domain, s.updated_at, s.current_version_id, v.params_schema
                    FROM scripts s
                    JOIN script_versions v ON v.id = s.current_version_id
                    WHERE s.id = ? AND s.status = 'active'
                    """,
                    (item.script_id,),
                ).fetchone()
                if row is None:
                    continue
                success_rate = self.script_success_rate(item.script_id)
                score, reason = rank_script_candidate(
                    semantic_similarity=float(item.score),
                    script_domain=row[1],
                    params=params,
                    success_rate=success_rate,
                    updated_at=str(row[2]),
                    params_schema=json.loads(row[4]),
                )
                ranked.append(
                    SearchCandidate(
                        script_id=str(row[0]),
                        version_id=str(row[3]),
                        score=score,
                        reason=reason,
                    )
                )

        ranked.sort(key=lambda candidate: candidate.score, reverse=True)
        return ranked[:limit]

    def update_embedding_text(self, script_id: str, embedding_text: str) -> None:
        self.vector_store.upsert(script_id, embedding_text)

    def count_execution_traces(self, execution_id: str) -> int:
        with self.sqlite.connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM execution_traces WHERE execution_id = ?",
                (execution_id,),
            ).fetchone()
        return int(row[0])

    def _id(self, prefix: str) -> str:
        return f"{prefix}_{uuid.uuid4().hex}"

    def _now(self) -> str:
        return datetime.now(timezone.utc).isoformat()
