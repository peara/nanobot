# Evaluator Prompt Testing

Iterate on evaluator LLM prompts without restarting the bot.

## Quick Start

```bash
uv run python scripts/eval/call_eval.py --list
uv run python scripts/eval/call_eval.py --fixture yahoo_search_failed_url
```

## CLI Reference

| Flag | Description |
|------|-------------|
| `--fixture NAME` | Use a saved input from `fixtures/` |
| `--last-log` | Use the last evaluator log entry |
| `--raw TEXT` | Use raw text as input |
| `--prompt PATH` | Override the default prompt with a file |
| `--phase PHASE` | Evaluator phase: `quality_assessment` (default) or `learning_extraction` |
| `--show-prompt` | Print the resolved prompt and exit |
| `--list` | List available fixtures and prompts |
| `--save-as NAME` | Save the result as a new fixture |

## Workflow

1. **Add a fixture** — drop a JSON file in `fixtures/`:
   ```json
   {
     "name": "my_scenario",
     "phase": "quality_assessment",
     "input": "User request:\n...\n\nAgent reply:\n...\n\nAgent scratchpad:\n...\n\nTool trace:\n..."
   }
   ```
   Or extract from the evaluator log with `--last-log --save-as my_scenario`.

2. **Iterate on a prompt** — edit `prompts/quality_assessment.txt`, then:
   ```bash
   uv run python scripts/eval/call_eval.py --fixture my_scenario --prompt prompts/quality_assessment.txt
   ```

3. **Promote to production** — copy the working prompt back into `src/nanobot/prompts/defaults.py`.

## Folder Layout

```
scripts/eval/
  call_eval.py       CLI entry point
  conf.py            Config, LLM client, schemas, fixture/prompt loading
  prompts/           Editable prompt variants (test without touching defaults.py)
  fixtures/          Saved evaluator inputs (real runs or hand-crafted)
```

## Extracting Fixtures from Real Runs

The bot writes full evaluator I/O to `data/evaluator.log`. To extract a fixture:

```bash
# View all quality_assessment entries in the log
uv run python scripts/eval/call_eval.py --last-log

# Save the last entry as a fixture
uv run python scripts/eval/call_eval.py --last-log --save-as my_scenario
```

Or manually copy the INPUT section from the log into a fixture JSON.