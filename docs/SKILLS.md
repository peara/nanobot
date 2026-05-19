# Skills

Reusable behaviors that the bot learns and injects into its own context at the right time.

## Overview

Skills are NanoBot's self-improvement mechanism. A skill is a named bundle of instructions (how to format search results, which tools to prefer for a task, what style to use) that gets injected into the agent's prompt when the trigger conditions match. Skills can be created manually through CRUD tools, or automatically by the [learning evaluator](EVALUATOR.md) after good conversations.

The skills system has three components:
- **SkillStore** (SQLite) — source of truth for skill definitions, trigger config, and metadata
- **SkillMatcher** — resolves which skills apply for a given input
- **SkillVectorStore** (Qdrant) — secondary semantic index for `intelligent` trigger mode

## Skill schema

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `id` | int | auto | Primary key (SQLite auto-increment) |
| `name` | string | required | Unique identifier, e.g. `"thai-language"` |
| `description` | string | required | Brief (~100 token) description of when this skill applies |
| `instructions` | string | required | Full skill content (~1000-5000 tokens) injected into the prompt |
| `trigger_mode` | string | `"pattern"` | How this skill activates: `always`, `pattern`, or `intelligent` |
| `trigger_patterns` | string[] | `[]` | Regex patterns for `pattern` mode (case-insensitive) |
| `tools_allowlist` | string[]? | `null` | Tool name patterns (fnmatch) this skill gates — matched tools are available when this skill is active |
| `priority` | int | `0` | Higher priority skills are included first when multiple match |
| `is_active` | bool | `true` | Whether this skill participates in matching |
| `created_at` | datetime | auto | Creation timestamp |
| `updated_at` | datetime | auto | Last-modified timestamp |

## Trigger modes

### `always`

Skill instructions are injected on every turn, regardless of what the user says. Use sparingly — every `always` skill consumes prompt tokens on every message.

### `pattern`

Skill activates when any of its `trigger_patterns` regex matches the user's message (case-insensitive). Good for command-like triggers: `"\\bweather\\b"`, `"\\btranslate\\b"`.

### `intelligent`

Skill activates when the user's goal is semantically similar to the skill's description, as determined by vector similarity search. No explicit patterns needed — the `SkillVectorStore` matches the goal against skill embeddings.

**How it works**: `SkillMatcher.find_by_intelligent(goal)` calls `SkillVectorStore.search_skills(goal)`, which embeds the goal, searches the `nanobot_skills` Qdrant collection, and returns matching skill names. Those names are then resolved to full `Skill` objects from SQLite.

**Re-indexing**: If the vector store is unavailable (no `config.mem0.yaml`), `intelligent` skills gracefully degrade — they simply won't match. Re-index with:

```bash
uv run python -m nanobot.debug_cli --config config.yaml skills-resync
```

## Matching flow

`SkillMatcher.find_relevant_skills(goal)` is the main entry point:

1. Collect `always` skills (all of them)
2. Collect `pattern` matches against the goal text (capped at `max_skills` per mode)
3. Collect `intelligent` matches via vector search (capped at `max_skills` per mode)
4. Deduplicate by name, sort by `priority` descending
5. Return at most `max_skills` (default: 5) skills

## Prompt injection

Matched skills are injected into the agent's message list as system messages, inserted after the primary system prompt. Each skill is rendered through the `skill_instructions` PromptStore template:

```
[Skill: {skill_name}]
{skill_description}

{skill_instructions}
```

Two budgets control injection size:
- **`MAX_SKILL_INSTRUCTIONS_CHARS = 5000`** — per-skill limit. Instructions exceeding this are clipped with `[truncated]`.
- **`MAX_TOTAL_SKILL_CHARS = 15000`** — total budget across all injected skills. Skills are added in priority order until the budget is exhausted.

## CRUD tools

The bot manages its own skills at runtime through 6 MCP tools:

| Tool | Purpose | Key params |
|------|---------|-----------|
| `skill__create` | Create a new skill | `name`, `description`, `instructions`, `trigger_mode`, `trigger_patterns`, `tools_allowlist`, `priority` |
| `skill__get` | Get full skill details | `name` or `skill_id` |
| `skill__update` | Update an existing skill | `name` (required), plus any updatable field |
| `skill__delete` | Delete a skill by name | `name` |
| `skill__list` | List skills | `active_only` (default: true) |
| `skill__activate` | Activate or deactivate a skill | `name`, `is_active` (default: true) |

### Qdrant sync

When `trigger_mode="intelligent"`, create/update/delete operations also sync to Qdrant:
- **create** → embeds `"{name}: {description}"` and upserts to `nanobot_skills` collection
- **update** → removes old embedding, stores new one (in case name/description changed)
- **delete** → removes embedding from Qdrant
- **activate** → SQLite only (no embedding change needed)

If the Qdrant sync fails, the SQLite operation still succeeds. The skill remains functional for `pattern` and `always` modes; only `intelligent` matching is affected.

## Evaluator-driven lifecycle

The [learning evaluator](EVALUATOR.md) can automatically create, update, or deprecate skills after each subagent turn. When `enable_evaluator: true` in config, the evaluator runs after every completed turn and may produce `SkillOperation` decisions that directly modify the skill store.

## Tool filtering

Not every tool is sent to the LLM on every call. Tools are gated by a **core + skill-allowlist** pattern system to keep the tool list under the ~20-tool accuracy threshold.

### Core tool set

`CORE_TOOL_PATTERNS` in `core.py` defines tools that are **always available** regardless of which skills are active:

| Pattern | Tools |
|---------|-------|
| `memory__search`, `memory__save`, `memory__save_turn` | 3 memory read/write tools |
| `skill__list`, `skill__get` | 2 skill discovery tools |
| `plan__get`, `plan__list` | 2 plan read-only tools |
| `timer__*` | All timer tools |
| `scheduler__*` | All scheduler tools |

`session__scratchpad_write` is also always available (prepended separately). This gives ~14-16 always-on tools — under the 20-tool threshold.

### Skill-gated tools

When a skill has `tools_allowlist` set, its patterns are **merged with core** when that skill is active. Tools not matching any core or active-skill pattern are invisible to the LLM.

**Example**: A `web_research` skill with `tools_allowlist: ["web__*", "playwright__*"]` makes web and browser tools available only when that skill is matched.

**`tools_allowlist = null`** → no additional tools beyond core for this skill.

### Computation flow

1. `SkillMatcher.find_relevant_skills(goal)` matches skills at spawn time
2. `_list_openai_tools(skill_names)` merges `CORE_TOOL_PATTERNS` + each active skill's `tools_allowlist`
3. `ToolRegistry.list_openai_specs(patterns)` filters via `fnmatch` union matching
4. Only matched tool schemas are sent to the LLM

### Current limitations

- Skills are matched **once at spawn time** — if the conversation topic shifts, new skills are not discovered mid-run
- There is no runtime tool for the LLM to request loading a skill it hasn't been matched with
- The Tier 1 skill catalog (`build_skill_catalog_message`) exists in code but is not wired into the prompt flow

## Storage

| Component | Backend | Location | Purpose |
|-----------|---------|----------|---------|
| SkillStore | SQLite | `skills` table | Definitions, triggers, metadata (source of truth) |
| SkillVectorStore | Qdrant | `nanobot_skills` collection | Semantic search index for `intelligent` mode |

The `skills` table has indexes on `name`, `is_active`, and `trigger_mode` for efficient querying.