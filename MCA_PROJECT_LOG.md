# PROJECT: Maximus Code Agent (MCA)
**Last Updated:** 2026-02-09
**Status:** Complete
**Location:** ~/maximus-code-agent/

---

## WHAT THIS PROJECT DOES
Local-first AI coding agent that uses structured function calling to execute tasks on a workspace. Connects to vLLM (Qwen 72B) for inference, PostgreSQL for memory/metrics/journaling, and Ollama for embeddings. Auto-journals its work, validates environment before acting, cleans up after itself, and saves progress continuously.

---

## CURRENT STATE
**Where we left off:** Reliability upgrade fully deployed and smoke-tested. Journal, preflight, cleanup, mass-fix detection, and continuous save all wired into orchestrator. Migration 7 (journal table) applied to PostgreSQL. 332 tests passing (8 skipped for live DB/Ollama).
**Next step:** None — project is complete. Monitor journal output on future `mca run` tasks.
**Blocking issue:** None

---

## ENVIRONMENT & TOOLS
| Tool | Version | Location | Notes |
|------|---------|----------|-------|
| Python | 3.13 | ~/maximus-code-agent/.venv/ | conda-managed venv |
| PostgreSQL | system | localhost:5432/openwebui | Schema: mca, user: maximus_user |
| vLLM | 0.15.0 | systemd service, port 8000 | Qwen/Qwen2.5-72B-Instruct-AWQ |
| Ollama | system | systemd service, port 11434 | nomic-embed-text for embeddings |
| psutil | installed | .venv | Required for preflight/cleanup |
| psycopg | v3 | .venv | PostgreSQL driver |

**Key commands to run this project:**
```bash
# Run a task:
PGPASSWORD=Arianna1 .venv/bin/python -m mca run "task description" --workspace . --mode auto

# Run tests:
PGPASSWORD=Arianna1 .venv/bin/python -m pytest tests/ -v

# Preflight check:
PGPASSWORD=Arianna1 .venv/bin/python -m mca preflight --workspace .

# Cleanup:
PGPASSWORD=Arianna1 .venv/bin/python -m mca cleanup --workspace .

# View journal:
PGPASSWORD=Arianna1 .venv/bin/python -m mca journal

# Metrics:
PGPASSWORD=Arianna1 .venv/bin/python -m mca metrics last
PGPASSWORD=Arianna1 .venv/bin/python -m mca metrics summary --days 7
```

**Key files:**
| File | Purpose | Last Modified |
|------|---------|---------------|
| src/mca/orchestrator/loop.py | Main orchestrator loop — all phases wired here | 2026-02-09 |
| src/mca/journal/writer.py | JournalWriter: DB + markdown run journaling | 2026-02-09 |
| src/mca/preflight/checks.py | PreflightRunner: 10 environment checks | 2026-02-09 |
| src/mca/cleanup/hygiene.py | CleanupRunner: orphans, temps, logs, journals | 2026-02-09 |
| src/mca/memory/migrations.py | 8 migrations (0-7), journal table is migration 7 | 2026-02-09 |
| src/mca/memory/pg_store.py | PostgreSQL store — all DB operations | 2026-02-09 |
| src/mca/cli.py | All CLI commands | 2026-02-09 |
| src/mca/config.py | YAML + env config, defaults for vLLM/PG/shell | stable |
| src/mca/tools/registry.py | 10 tools, 26 actions, build_registry() factory | stable |
| src/mca/llm/client.py | vLLM OpenAI-compatible client with retry/streaming | stable |

---

## CONFIGURATION (WORKING)
**DO NOT CHANGE THESE without documenting why.**

```
# PostgreSQL connection (required for all mca commands):
PGPASSWORD=Arianna1
User: maximus_user
DB: openwebui
Schema: mca
Host: localhost:5432

# vLLM (default in config.py):
base_url: http://localhost:8000/v1
model: Qwen/Qwen2.5-72B-Instruct-AWQ
temperature: 0.3 (DO NOT use 0 — causes ~10% accuracy degradation)

# Ollama embeddings:
Model: nomic-embed-text (768-dim)
Port: 11434

# Orchestrator constants:
MAX_ITERATIONS: 15
_CHECKPOINT_EVERY_N: 3 (auto-save interval)
Spike mode threshold: confidence < 40 triggers ask mode override

# Preflight thresholds:
Disk: fail <2GB, warn <10GB
RAM: warn <4GB
Temps: warn >100 files
Logs: warn >50MB

# Cleanup thresholds:
Temps: remove >24h old
Logs: rotate >50MB, keep 3
Journals: prune >30 days
```

---

## CHANGE LOG
Record EVERY change. Never make silent edits.

### 2026-02-09 - Reliability upgrade: journal, preflight, cleanup, mass-fix, continuous save
- **What changed:** Added 3 new modules (journal/, preflight/, cleanup/), migration 7 (mca.journal table), pg_store journal methods, orchestrator integration (all phases), 3 CLI commands, 94 new tests
- **Why:** Upgrade MCA from task executor to self-monitoring agent that journals work, validates environment, cleans up, and saves continuously
- **Result:** 332 tests passing, all features working end-to-end. Smoke-tested with mca preflight, mca cleanup, mca journal, and mca run.
- **Reverted?** No
- **Commits:** 61801d6 (main implementation), caa7240 (revert spike bypass)
