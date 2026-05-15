# Learning Evaluator

Three-phase pipeline that turns good conversations into reusable skills.

## Overview

After each completed subagent turn, the evaluator decides whether the interaction produced something worth remembering. It runs in three phases — quality assessment, learning extraction, and skill lifecycle — each gated by the previous result. If the quality gate fails, no LLM calls are wasted on the later phases.

The evaluator is off by default. Enable it with `enable_evaluator: true` in `config.yaml`.

## The pipeline

```
Subagent turn completes
        │
        ▼
Phase 1: Quality Assessment
        │   has_learnings=false → STOP (return early)
        │   has_learnings=true
        ▼
Phase 2: Learning Extraction
        │   learnings empty → STOP
        │   learnings found
        │   filter: remove low-confidence → all low? → STOP
        │   has actionable items (medium/high confidence)
        ▼
Phase 3: Skill Lifecycle
        │   produce SkillOperation list (create/update/skip)
        ▼
BotCore._execute_skill_decisions()
```

Each phase is a separate LLM call with structured JSON output (using `response_format` / JSON schema enforcement). The pipeline is **sequentially gated**: later phases only run when earlier phases indicate there's something worth extracting.

### Phase 1: Quality Assessment

**Input** — built from the subagent turn:
- User request text
- Agent reply text
- Optional: scratchpad summary (goal, context, current_step, known_facts, tool_journal)
- Optional: run failure status + error message
- Optional: tool trace summary (all tool calls with args + result previews, excluding `session__scratchpad_write`)

**Output** — `QualityAssessment`:
- `quality_score`: 1-5 (1 = failed, 5 = excellent)
- `quality_reason`: brief explanation
- `has_learnings`: boolean — **the critical gate**
- `confidence`: "high" | "medium" | "low"

The `has_learnings` field is independent of the quality score. A high-quality answer can still produce learnings (e.g., the user revealed a preference). A low-quality answer might still yield useful observations about what went wrong.

**Fallback**: If the LLM response is empty, `{}`, or truncated (`finish_reason == "length"`), returns a default with `has_learnings=False`, preventing wasted Phase 2 and 3 calls.

### Phase 2: Learning Extraction

**Input** — same as Phase 1, plus:
- Active skills list (name + description for each) — so the LLM can avoid duplicating existing skills
- More detailed scratchpad and tool trace context

**Output** — `LearningExtraction` containing a list of `LearningItem`:
- `category`: "user_preference" | "workflow_pattern" | "constraint"
- `observation`: what was learned (raw text)
- `direction`: "create_skill" | "update_skill" | "deprecate_skill"
- `evidence`: quote or paraphrase supporting the learning
- `confidence`: "high" | "medium" | "low"

**Post-extraction filter**: Only items with `confidence != "low"` proceed to Phase 3. If all learnings are low-confidence, the pipeline stops here.

### Phase 3: Skill Lifecycle

**Input**:
- Actionable learnings (medium + high confidence only): category, direction, confidence, observation, evidence
- Active skills list (name + description for each)

**Output** — list of `SkillOperation`:
- `action`: "create" | "update" | "skip"
- `name`: snake_case skill identifier
- `description`: brief sentence for semantic matching
- `instructions`: content to inject when the skill activates
- `trigger_mode`: "intelligent" (default) | "pattern" | "always"
- `source_confidence`: propagated from the learning
- `reason`: brief explanation

The LLM is prompted to be conservative: skip unless the pattern is clearly reusable, prefer update over create to avoid duplication, and default to `intelligent` trigger mode.

## Executing decisions

After the evaluator returns, `BotCore._execute_skill_decisions()` processes each `SkillOperation`:

| Action | Behavior |
|--------|----------|
| `skip` | Logged, no action taken |
| `create` | Checks `SkillStore.get_by_name()` — skips if name already exists (no duplicates). Otherwise creates the skill + syncs to Qdrant if `trigger_mode == "intelligent"` |
| `update` | Checks `SkillStore.get_by_name()` — skips if name doesn't exist. Otherwise updates changed fields + re-syncs to Qdrant (remove old embedding, store new) |

Each operation is processed independently in its own try/except block. A failure in one operation does not block others.

## Fault tolerance

The evaluator has layered fault tolerance:

1. **Whole-pipeline** — The entire `evaluate()` call is wrapped in a broad `except Exception` in `BotCore._evaluate_turn()`. If anything crashes, the error is logged and normal processing continues. The bot never stops due to evaluator failure.

2. **Per-phase truncation** — Each LLM call checks `finish_reason == "length"`. Truncated responses produce safe defaults (Phase 1: `has_learnings=False`; Phase 2: empty learnings; Phase 3: empty decisions), preventing corrupt data from flowing downstream.

3. **Per-decision isolation** — In `_execute_skill_decisions()`, each `SkillOperation` is processed in its own try/except. One bad skill decision cannot block others.

4. **Qdrant sync resilience** — If `SkillVectorStore.store_skill()` or `remove_skill()` fails, the local SQLite skill is still created/updated. Only intelligent matching is affected.

5. **Guard conditions** — Phase 2 → Phase 3: low-confidence learnings removed. Create: skips if skill name exists. Update: skips if skill name doesn't exist.

## Logging

Evaluator LLM I/O is logged to `data/evaluator.log` (rotating, 2MB × 3 backups) via a dedicated logger (`nanobot.evaluator.io`). Each phase logs scope, phase name, input, and raw LLM response. This is useful for debugging prompt quality and tracking what the evaluator learns.

## Prompt templates

| Key | Purpose |
|-----|---------|
| `quality_assessment` | Phase 1 system prompt |
| `learning_extraction` | Phase 2 system prompt |
| `skill_lifecycle` | Phase 3 system prompt |

All three are loaded through PromptStore. If a custom template isn't found, defaults from `nanobot.prompts.defaults` are used. Override by adding entries to the `prompts` table in SQLite.

## Config

```yaml
enable_evaluator: true  # In config.yaml
```

The evaluator uses the same LLM client as the main agent (configured in `model` settings). It sends structured JSON requests with `response_format` set to the appropriate JSON schema for each phase.

## Relationship to skills

See [SKILLS.md](SKILLS.md) for how skills are stored, matched, and injected. The evaluator is the primary automatic path for skill creation — but skills can also be created manually through the `skill__create` tool or the debug CLI.