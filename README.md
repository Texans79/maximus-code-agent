# Maximus Code Agent (MCA)

A local-first AI coding agent with safety rails, multi-agent review pipeline, and hardware-aware telemetry.

## Features

- **Workspace Jail**: All file operations confined to a configured workspace. Blocks traversal, symlinks, and absolute paths outside it.
- **Diff-Only Edits**: File modifications applied as unified diffs (patches). No blind full rewrites.
- **Shell Safety**: Denylist for dangerous commands (`rm -rf /`, `mkfs`, `dd`, `curl|bash`, etc.). Allowlist option. Full command logging.
- **Approval Modes**: `auto` / `ask` / `paranoid` — control exactly what the agent can do.
- **Git Checkpoints**: Every task creates a checkpoint commit; on failure, automatic rollback. `mca rollback` to undo.
- **Verification Loop**: Plan → Edit → Run → Fix → Run. Iterates until tests pass.
- **Secret Redaction**: Env values and tokens are never printed in logs.
- **Multi-Agent Pipeline**: Planner → Implementer → Reviewer → Tester chain. Reviewer blocks if tests are missing.
- **Hardware Telemetry**: CPU, RAM, Disk, GPU (nvidia-smi), NVMe temps via `mca status`.
- **Long-Term Memory**: SQLite (default) or Postgres+pgvector. Store project decisions, recipes, embeddings.
- **Project Templates**: `python-cli`, `fastapi`, `node-ts`, `docker-service`.
- **Telegram Control**: `/run`, `/status`, `/rollback`, `/logs`, `/approve`, `/deny`.

## Install

```bash
# Clone
git clone <repo-url> && cd maximus-code-agent

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

### Run a coding task

```bash
# Auto mode (no approval prompts)
mca run "Add a power() function to app.py" --workspace ./demo_repo --mode auto

# Ask mode (approve plan before execution)
mca run "Fix the bug in auth.py" --workspace ./my-project --mode ask

# Paranoid mode (approve every file write and command)
mca run "Refactor the database module" --mode paranoid
```

### System status

```bash
mca status
```

Output:
```
┌────────────────────────────────┐
│        System Status           │
├──────────────┬─────────────────┤
│ CPU          │ AMD Threadripper│
│   Load (1m)  │        12.3%   │
│ RAM          │ 48/512 GB (9%) │
│ GPU 0        │ RTX 6000 Ada   │
│   Temp       │          45°C  │
│   Util       │           23%  │
│   VRAM       │ 12000/49140 MB │
└──────────────┴─────────────────┘
```

### Scaffold a new project

```bash
mca init --template python-cli --name my-tool
mca init --template fastapi --name my-api
mca init --template node-ts --name my-app
mca init --template docker-service --name my-svc
```

### Memory

```bash
# Store a decision
mca memory add "Use SQLite for local storage, Postgres for production" --tags "architecture,database"

# Search
mca memory search "database storage"
```

### Rollback

```bash
mca rollback --workspace ./my-project
```

### Telegram bot

```bash
export MCA_TELEGRAM_TOKEN="your-bot-token"
mca telegram start
```

Bot commands: `/status`, `/run <task>`, `/rollback`, `/logs`, `/approve`, `/deny`

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
    # ... (defaults are comprehensive)
  allowlist: []

git:
  auto_checkpoint: true

memory:
  backend: sqlite  # or postgres
  sqlite_path: ".mca/memory.db"
  postgres_dsn: "postgresql://user:pass@localhost/mca"

style:
  indent: 4
  quotes: double
  docstrings: google
```

Environment variable overrides:
- `MCA_WORKSPACE` — workspace directory
- `MCA_APPROVAL_MODE` — approval mode
- `MCA_LLM_BASE_URL` — LLM endpoint
- `MCA_LLM_MODEL` — model name
- `MCA_LLM_API_KEY` — API key
- `MCA_TELEGRAM_TOKEN` — Telegram bot token

## Safety

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

## Architecture

```
src/mca/
├── cli.py              # Typer CLI commands
├── config.py           # YAML + env config loader
├── log.py              # Structured JSON + console logging
├── tools/
│   ├── safe_fs.py      # Workspace jail + diff/patch
│   ├── safe_shell.py   # Command denylist + logging
│   └── git_ops.py      # Checkpoints + rollback
├── orchestrator/
│   ├── loop.py         # Plan→edit→run→fix loop
│   ├── approval.py     # Auto/ask/paranoid modes
│   └── agents.py       # Multi-agent pipeline
├── telemetry/
│   └── collectors.py   # CPU/RAM/GPU/NVMe
├── memory/
│   ├── base.py         # Store interface + factory
│   ├── sqlite_store.py # SQLite + FTS5
│   └── pg_store.py     # Postgres + pgvector
├── templates/
│   └── registry.py     # Project scaffolding
└── telegram/
    └── bot.py          # Telegram bot
```

## Testing

```bash
# Run all tests
pytest -v

# Run specific test module
pytest tests/test_safe_fs.py -v
```

## Demo

```bash
# Run demo tests
cd demo_repo && python -m pytest tests/ -v && cd ..

# Use MCA on the demo repo
mca run "Add a power(base, exp) function to app.py with tests" \
    --workspace ./demo_repo --mode auto
```

## License

MIT
