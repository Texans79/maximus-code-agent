# Maximus Code Agent (MCA)

A local-first AI coding agent with structured function calling, safety rails, multi-agent review pipeline, and hardware-aware telemetry. Runs entirely on your own hardware via vLLM.

## Features

- **Structured Function Calling**: 26 tool definitions with JSON Schema, passed to the LLM via the `tools` parameter.
- **Done Validation**: The agent cannot call `done()` unless tests have actually passed. Prevents hallucinated success.
- **Run Metrics**: Every run records success/iterations/tool_calls/files_changed/tests/tokens to PostgreSQL.
- **Workspace Jail**: All file operations confined to a configured workspace. Blocks traversal, symlinks, and absolute paths outside it.
- **Shell Safety**: Denylist for dangerous commands (`rm -rf /`, `mkfs`, `dd`, `curl|bash`, etc.).
- **Approval Modes**: `auto` / `ask` / `paranoid` — control exactly what the agent can do.
- **Git Checkpoints**: Every task creates a checkpoint; on failure, automatic rollback.
- **Long-Term Memory**: PostgreSQL + pgvector (primary) with SQLite fallback. Stores decisions, recipes, embeddings.
- **Memory Recall**: Before each task, MCA searches past work for similar context and injects it into the prompt.
- **Token Tracking**: Cumulative prompt/completion token counting across all LLM calls.
- **Streaming**: `chat_stream()` for real-time token output.
- **Secret Redaction**: Env values and tokens are never printed in logs.
- **Hardware Telemetry**: CPU, RAM, Disk, GPU (nvidia-smi), NVMe temps.
- **Telegram Bot**: `/run`, `/status`, `/memory`, `/rollback`, `/logs` — control MCA from your phone.

## Prerequisites

```bash
cd ~/maximus-code-agent
export PGPASSWORD=Arianna1
source .venv/bin/activate
```

All commands below assume the venv is activated and `PGPASSWORD` is set.

## Install

```bash
# Clone
git clone https://github.com/Texans79/maximus-code-agent.git && cd maximus-code-agent

# Create env (Python 3.11+)
python -m venv .venv && source .venv/bin/activate

# Install
pip install -e .

# With optional extras
pip install -e ".[telegram]"   # Telegram bot
pip install -e ".[pg]"         # Postgres + pgvector
pip install -e ".[all]"        # Everything
```

## Quick Start

```bash
# Verify everything works
mca llm ping
mca tools verify -w ./demo_repo
mca metrics summary

# Run a task
mca run "add feature X" -w /path/to/project -m auto

# Check what happened
mca metrics last
```

---

## Core Command: Run a Task

```bash
mca run "your task description" --workspace /path/to/project --mode auto
```

| Flag | Default | Options |
|---|---|---|
| `--workspace, -w` | Current dir | Any project directory |
| `--mode, -m` | `ask` | `auto` (no prompts), `ask` (confirm plan), `paranoid` (confirm everything) |
| `--verbose, -v` | off | Enable debug logging |

**What happens when you run a task:**

1. Connects to PostgreSQL, creates a task record
2. Git checkpoint (auto-saves current state)
3. Recalls similar past work from pgvector memory
4. LLM plans the approach (in `ask` mode, you approve the plan)
5. LLM executes tools in a loop (up to 15 iterations): reads files, writes code, runs tests
6. **Done validation** — MCA cannot finish until tests pass
7. On success: git checkpoint, stores outcome in memory, writes run metrics
8. On failure: auto-rollback to the checkpoint, records failure reason

**Example runs:**

```bash
mca run "add a login endpoint with JWT auth" -w ./my-api -m auto
mca run "fix the failing test in test_utils.py" -w ./my-project -m auto
mca run "refactor database queries to use connection pooling" -w ./backend -m ask
```

---

## All CLI Commands

### Tool & System Commands

```bash
# List all 9 tools and 26 actions available to the agent
mca tools list --workspace ./project

# Verify all tools are functional
mca tools verify --workspace ./project

# Check system health (CPU, RAM, GPU, disk, NVMe)
mca status

# Detect and run project tests
mca test --workspace ./project

# Scaffold a new project from template
mca init --template python-cli --name my-app --dest ./my-app
# Templates: python-cli, fastapi, node-ts, docker-service
```

### LLM & Embedding Commands

```bash
# Verify vLLM is running
mca llm ping

# Generate an embedding vector (test Ollama connection)
mca embed "some text to embed"
```

### Memory Commands

```bash
# Store knowledge for future recall (auto-embeds via Ollama)
mca memory add "Always use connection pooling for Postgres" --tags "postgres,pattern" --category recipe

# Full-text search of stored knowledge
mca memory search "postgres connection"

# Vector similarity recall (pgvector)
mca memory recall "fix database timeout issue" --limit 5
```

Categories: `general`, `decision`, `recipe`, `pattern`, `error`, `context`

### Metrics Commands

```bash
# Show most recent run
mca metrics last

# Show last N runs
mca metrics last --count 5

# Aggregate stats over a time period
mca metrics summary --days 7
mca metrics summary --days 30

# List failed runs
mca metrics failures --days 30
```

Metrics tracked per run: success, iterations, tool_calls, files_changed, tests_runs, lint_runs, rollback_used, failure_reason, model, token_prompt, token_completion, duration.

### Git Safety

```bash
# Rollback the last MCA checkpoint (undoes MCA's changes)
mca rollback --workspace ./project
```

MCA auto-checkpoints before and after every run. If something goes wrong, `mca rollback` reverts to the pre-task state.

### Telegram Bot (Optional)

```bash
# Set token first
export MCA_TELEGRAM_TOKEN=your-bot-token

# Install dependency (one-time)
pip install python-telegram-bot

# Start bot
mca telegram start --workspace ./project
```

Bot commands: `/run <task>`, `/status`, `/memory <query>`, `/rollback`, `/logs`

---

## Tools (26 actions across 10 tools)

These are the tools MCA can call during a task. The LLM decides which tools to call. It **cannot call done()** until the most recent test run passes.

| Tool | Actions | Description |
|------|---------|-------------|
| **filesystem** | read_file, write_file, replace_in_file, edit_file, search, list_files | Workspace-jailed file operations |
| **shell** | run_command | Execute commands with safety denylist |
| **git** | git_checkpoint, git_rollback, git_branch, git_diff, git_log | Version control with auto-checkpoint |
| **done** | done | Signal task completion (validated — tests must pass) |
| **telemetry** | system_status | CPU/RAM/GPU/NVMe telemetry |
| **memory** | memory_add, memory_search | Long-term knowledge store |
| **test_runner** | run_tests, detect_test_framework | Auto-detect and run pytest/jest/go/cargo |
| **repo_indexer** | index_repo, find_entrypoints, parse_dependencies | Map repo structure |
| **linter** | lint, format_code, detect_linters | Run ruff/eslint/prettier |
| **dep_doctor** | check_environment, check_python, check_node, check_go | Verify environment health |

---

## Configuration

Create `.mca/config.yaml` in your project:

```yaml
workspace: "."
approval_mode: ask  # auto | ask | paranoid

llm:
  base_url: "http://localhost:8000/v1"  # vLLM, OpenAI, etc.
  model: "Qwen/Qwen2.5-72B-Instruct-AWQ"
  api_key: "not-needed"  # for local vLLM
  temperature: 0.3

shell:
  timeout: 120
  denylist:
    - "rm -rf /"
    - "mkfs"
  allowlist: []

git:
  auto_checkpoint: true

memory:
  backend: postgres  # or sqlite
  postgres_dsn: "postgresql://maximus_user@localhost:5432/openwebui"

style:
  indent: 4
  quotes: double
  docstrings: google
```

Environment variable overrides:
- `VLLM_BASE_URL` — LLM endpoint (default: `http://localhost:8000/v1`)
- `VLLM_MODEL` — model name
- `VLLM_API_KEY` — API key
- `EMBEDDING_BASE_URL` — Ollama endpoint (default: `http://localhost:11434`)
- `EMBEDDING_MODEL` — embedding model (default: `nomic-embed-text`)
- `MCA_TELEGRAM_TOKEN` — Telegram bot token
- `PGPASSWORD` — PostgreSQL password for `maximus_user`

---

## Safety

### Done validation

The agent cannot call `done()` to signal task completion unless:
1. Tests have been run (at least one `run_tests` call)
2. The most recent test run passed (exit code 0)

If the LLM tries to call `done()` without passing tests, the call is rejected and the agent is told to fix the issues first.

### What's blocked by default

| Pattern | Why |
|---------|-----|
| `rm -rf /` | System destruction |
| `mkfs`, `dd if=` | Disk wipe |
| `shutdown`, `reboot` | System control |
| `chmod -R 777` | Permission escalation |
| `curl\|bash`, `wget\|bash` | Remote code execution |
| `:(){ :\|:& };:` | Fork bomb |

### Workspace jail

- All file reads/writes are resolved and verified against the workspace root.
- Symlinks that resolve outside the workspace are blocked.
- Path traversal (`../../../etc/passwd`) is blocked.
- Absolute paths outside workspace are blocked.

### Git safety

- Every task starts with a checkpoint commit + tag.
- On failure, automatic rollback to checkpoint.
- `mca rollback` always available.

### Secret protection

- Environment variables matching `TOKEN`, `SECRET`, `KEY`, `PASS`, `AUTH` are redacted in all logs.
- Known patterns (JWT, GitHub tokens, AWS keys) are scrubbed from output.

---

## Architecture

```
src/mca/
├── cli.py              # Typer CLI (run, status, tools, llm, memory, metrics, embed, test)
├── config.py           # YAML + env config loader
├── log.py              # Structured JSON + console logging
├── tools/
│   ├── base.py         # ToolBase ABC, ToolResult, _param helper
│   ├── registry.py     # ToolRegistry + build_registry() factory
│   ├── fs_tool.py      # FSTool — workspace-jailed file I/O (6 actions)
│   ├── safe_fs.py      # SafeFS — jail, search, diff, replace_in_file
│   ├── shell_tool.py   # ShellTool — command execution
│   ├── safe_shell.py   # SafeShell — denylist, timeout, logging
│   ├── git_tool.py     # GitTool — checkpoint, rollback, branch, diff, log
│   ├── git_ops.py      # GitOps — git subprocess wrappers
│   ├── done_tool.py    # DoneTool — task completion signal (validated)
│   ├── telemetry_tool.py # TelemetryTool — system metrics
│   ├── memory_tool.py  # MemoryTool — knowledge store access
│   ├── test_runner.py  # TestRunner — detect & run pytest/jest/go/cargo
│   ├── repo_indexer.py # RepoIndexer — entrypoints, deps, file types
│   ├── linter.py       # LinterFormatter — ruff, eslint, prettier
│   └── dep_doctor.py   # DepDoctor — Python/Node/Go env health
├── llm/
│   ├── __init__.py
│   └── client.py       # LLMClient — vLLM chat + streaming + token tracking
├── orchestrator/
│   ├── loop.py         # Main loop: function calling, tool dispatch, validation, metrics
│   ├── approval.py     # Auto/ask/paranoid modes
│   └── agents.py       # Multi-agent pipeline (Planner/Implementer/Reviewer/Tester)
├── memory/
│   ├── base.py         # MemoryStore interface + factory
│   ├── sqlite_store.py # SQLite + FTS5 fallback
│   ├── pg_store.py     # PostgreSQL + pgvector (primary)
│   ├── migrations.py   # Schema migrations (5 versions)
│   ├── embeddings.py   # Embedder — Ollama nomic-embed-text (768-dim)
│   ├── recall.py       # recall_similar() + store_outcome()
│   └── metrics.py      # Run metrics — write, query last/summary/failures
├── telemetry/
│   └── collectors.py   # CPU/RAM/GPU/NVMe data collection
├── templates/
│   └── registry.py     # Project scaffolding (python-cli, fastapi, node-ts, docker-service)
├── telegram/
│   └── bot.py          # Telegram bot (async, non-blocking task execution)
└── utils/
    └── secrets.py      # Secret redaction patterns
```

## Database Schema (mca.*)

| Table | Purpose |
|-------|---------|
| migrations | Schema version tracking |
| tasks | Agent task records |
| steps | Individual steps within tasks |
| artifacts | Files created/modified by tasks |
| knowledge | Long-term memory with pgvector embeddings (768-dim) |
| tools | Tool execution log |
| evaluations | Reviewer verdicts and test results |
| run_metrics | Per-run telemetry (success, iterations, tokens, etc.) |

## Testing

```bash
# Run all tests (200 tests)
PGPASSWORD=Arianna1 pytest tests/ -v

# Run specific test module
pytest tests/test_safe_fs.py -v
pytest tests/test_orchestrator.py -v
pytest tests/test_metrics.py -v
pytest tests/test_llm_client.py -v
```

## Demo

```bash
# Use MCA on the demo repo
mca run "Add a power(base, exp) function to app.py with tests" \
    --workspace ./demo_repo --mode auto

# Check results
mca metrics last
```

## License

MIT
