"""PostgreSQL schema migrations for MCA memory tables.

All tables live in the 'mca' schema to avoid collisions with other applications
sharing the same database.

Tables:
  mca.tasks        — agent tasks (coding sessions)
  mca.steps        — individual steps within a task
  mca.artifacts    — files created/modified by tasks
  mca.knowledge    — long-term memory entries with embeddings (pgvector)
  mca.tools        — tool execution log
  mca.evaluations  — quality evaluations (reviewer verdicts, test results)
"""
from __future__ import annotations

MIGRATIONS: list[str] = [
    # Migration 0: schema + extensions
    """\
    CREATE SCHEMA IF NOT EXISTS mca;
    CREATE EXTENSION IF NOT EXISTS vector;
    CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
    """,

    # Migration 1: core tables
    """\
    CREATE TABLE IF NOT EXISTS mca.migrations (
        version  INTEGER PRIMARY KEY,
        applied  TIMESTAMPTZ NOT NULL DEFAULT NOW()
    );

    CREATE TABLE IF NOT EXISTS mca.tasks (
        id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
        description TEXT NOT NULL,
        status      TEXT NOT NULL DEFAULT 'pending'
                    CHECK (status IN ('pending','running','completed','failed','rolled_back')),
        workspace   TEXT NOT NULL DEFAULT '',
        config      JSONB NOT NULL DEFAULT '{}',
        result      JSONB,
        created     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated     TIMESTAMPTZ NOT NULL DEFAULT NOW()
    );

    CREATE TABLE IF NOT EXISTS mca.steps (
        id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
        task_id     UUID NOT NULL REFERENCES mca.tasks(id) ON DELETE CASCADE,
        seq         INTEGER NOT NULL DEFAULT 0,
        agent_role  TEXT NOT NULL DEFAULT 'orchestrator',
        action      TEXT NOT NULL,
        input       JSONB,
        output      JSONB,
        status      TEXT NOT NULL DEFAULT 'pending'
                    CHECK (status IN ('pending','running','completed','failed')),
        duration_ms INTEGER,
        created     TIMESTAMPTZ NOT NULL DEFAULT NOW()
    );
    CREATE INDEX IF NOT EXISTS idx_steps_task ON mca.steps(task_id, seq);

    CREATE TABLE IF NOT EXISTS mca.artifacts (
        id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
        task_id     UUID REFERENCES mca.tasks(id) ON DELETE SET NULL,
        step_id     UUID REFERENCES mca.steps(id) ON DELETE SET NULL,
        path        TEXT NOT NULL,
        action      TEXT NOT NULL CHECK (action IN ('created','modified','deleted')),
        diff        TEXT,
        content_hash TEXT,
        created     TIMESTAMPTZ NOT NULL DEFAULT NOW()
    );
    CREATE INDEX IF NOT EXISTS idx_artifacts_task ON mca.artifacts(task_id);
    CREATE INDEX IF NOT EXISTS idx_artifacts_path ON mca.artifacts(path);

    CREATE TABLE IF NOT EXISTS mca.knowledge (
        id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
        content     TEXT NOT NULL,
        tags        TEXT[] NOT NULL DEFAULT '{}',
        project     TEXT NOT NULL DEFAULT '',
        category    TEXT NOT NULL DEFAULT 'general'
                    CHECK (category IN ('general','decision','recipe','pattern','error','context')),
        metadata    JSONB NOT NULL DEFAULT '{}',
        embedding   vector(384),
        created     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated     TIMESTAMPTZ NOT NULL DEFAULT NOW()
    );
    CREATE INDEX IF NOT EXISTS idx_knowledge_tags ON mca.knowledge USING gin(tags);
    CREATE INDEX IF NOT EXISTS idx_knowledge_project ON mca.knowledge(project);
    CREATE INDEX IF NOT EXISTS idx_knowledge_category ON mca.knowledge(category);
    CREATE INDEX IF NOT EXISTS idx_knowledge_fts ON mca.knowledge
        USING gin(to_tsvector('english', content));

    CREATE TABLE IF NOT EXISTS mca.tools (
        id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
        task_id     UUID REFERENCES mca.tasks(id) ON DELETE SET NULL,
        step_id     UUID REFERENCES mca.steps(id) ON DELETE SET NULL,
        tool_name   TEXT NOT NULL,
        command     TEXT,
        exit_code   INTEGER,
        stdout      TEXT,
        stderr      TEXT,
        duration_ms INTEGER,
        created     TIMESTAMPTZ NOT NULL DEFAULT NOW()
    );
    CREATE INDEX IF NOT EXISTS idx_tools_task ON mca.tools(task_id);

    CREATE TABLE IF NOT EXISTS mca.evaluations (
        id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
        task_id     UUID REFERENCES mca.tasks(id) ON DELETE CASCADE,
        step_id     UUID REFERENCES mca.steps(id) ON DELETE SET NULL,
        evaluator   TEXT NOT NULL DEFAULT 'reviewer',
        verdict     TEXT NOT NULL CHECK (verdict IN ('approve','reject','request_changes')),
        issues      JSONB NOT NULL DEFAULT '[]',
        comments    TEXT,
        created     TIMESTAMPTZ NOT NULL DEFAULT NOW()
    );
    CREATE INDEX IF NOT EXISTS idx_evaluations_task ON mca.evaluations(task_id);
    """,

    # Migration 2: vector similarity index (IVFFlat requires rows to exist first;
    # we use HNSW which doesn't)
    """\
    DROP INDEX IF EXISTS mca.idx_knowledge_embedding;
    CREATE INDEX IF NOT EXISTS idx_knowledge_embedding_hnsw
        ON mca.knowledge USING hnsw (embedding vector_cosine_ops);
    """,

    # Migration 3: expand embedding dimension from 384 to 768
    # (nomic-embed-text outputs 768-dim vectors)
    """\
    DROP INDEX IF EXISTS mca.idx_knowledge_embedding_hnsw;
    ALTER TABLE mca.knowledge ALTER COLUMN embedding TYPE vector(768);
    CREATE INDEX IF NOT EXISTS idx_knowledge_embedding_hnsw
        ON mca.knowledge USING hnsw (embedding vector_cosine_ops);
    """,

    # Migration 4: run metrics table for reliability telemetry
    """\
    CREATE TABLE IF NOT EXISTS mca.run_metrics (
        id               UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
        task_id          UUID REFERENCES mca.tasks(id) ON DELETE SET NULL,
        started_at       TIMESTAMPTZ NOT NULL,
        ended_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        success          BOOLEAN NOT NULL DEFAULT FALSE,
        iterations       INTEGER NOT NULL DEFAULT 0,
        tool_calls       INTEGER NOT NULL DEFAULT 0,
        files_changed    INTEGER NOT NULL DEFAULT 0,
        tests_runs       INTEGER NOT NULL DEFAULT 0,
        lint_runs        INTEGER NOT NULL DEFAULT 0,
        rollback_used    BOOLEAN NOT NULL DEFAULT FALSE,
        failure_reason   TEXT,
        model            TEXT,
        token_prompt     INTEGER NOT NULL DEFAULT 0,
        token_completion INTEGER NOT NULL DEFAULT 0
    );
    CREATE INDEX IF NOT EXISTS idx_run_metrics_task ON mca.run_metrics(task_id);
    CREATE INDEX IF NOT EXISTS idx_run_metrics_started ON mca.run_metrics(started_at DESC);
    CREATE INDEX IF NOT EXISTS idx_run_metrics_success ON mca.run_metrics(success);
    """,

    # Migration 5: confidence scoring columns on run_metrics
    """\
    ALTER TABLE mca.run_metrics ADD COLUMN IF NOT EXISTS confidence_score INTEGER;
    ALTER TABLE mca.run_metrics ADD COLUMN IF NOT EXISTS spike_mode BOOLEAN DEFAULT FALSE;
    """,

    # Migration 6: knowledge graph tables
    """\
    CREATE TABLE IF NOT EXISTS mca.graph_nodes (
        id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
        workspace   TEXT NOT NULL,
        node_type   TEXT NOT NULL
                    CHECK (node_type IN ('file','function','class','module','dependency','endpoint','table')),
        name        TEXT NOT NULL,
        file_path   TEXT,
        line_number INTEGER,
        metadata    JSONB NOT NULL DEFAULT '{}',
        created     TIMESTAMPTZ NOT NULL DEFAULT NOW()
    );
    CREATE UNIQUE INDEX IF NOT EXISTS idx_graph_nodes_unique
        ON mca.graph_nodes (workspace, node_type, name, COALESCE(file_path, ''));
    CREATE INDEX IF NOT EXISTS idx_graph_nodes_workspace ON mca.graph_nodes(workspace);
    CREATE INDEX IF NOT EXISTS idx_graph_nodes_type ON mca.graph_nodes(node_type);
    CREATE INDEX IF NOT EXISTS idx_graph_nodes_name ON mca.graph_nodes(name);

    CREATE TABLE IF NOT EXISTS mca.graph_edges (
        id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
        source_id   UUID NOT NULL REFERENCES mca.graph_nodes(id) ON DELETE CASCADE,
        target_id   UUID NOT NULL REFERENCES mca.graph_nodes(id) ON DELETE CASCADE,
        edge_type   TEXT NOT NULL
                    CHECK (edge_type IN ('imports','calls','extends','implements','depends_on','contains','defines')),
        weight      REAL NOT NULL DEFAULT 1.0,
        metadata    JSONB NOT NULL DEFAULT '{}',
        created     TIMESTAMPTZ NOT NULL DEFAULT NOW()
    );
    CREATE UNIQUE INDEX IF NOT EXISTS idx_graph_edges_unique
        ON mca.graph_edges (source_id, target_id, edge_type);
    CREATE INDEX IF NOT EXISTS idx_graph_edges_source ON mca.graph_edges(source_id);
    CREATE INDEX IF NOT EXISTS idx_graph_edges_target ON mca.graph_edges(target_id);
    CREATE INDEX IF NOT EXISTS idx_graph_edges_type ON mca.graph_edges(edge_type);
    """,

    # Migration 7: journal table for run journaling
    """\
    CREATE TABLE IF NOT EXISTS mca.journal (
        id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        task_id     UUID REFERENCES mca.tasks(id) ON DELETE SET NULL,
        run_id      UUID NOT NULL,
        seq         INTEGER NOT NULL,
        phase       TEXT NOT NULL
                    CHECK (phase IN ('start','preflight','plan','tool','checkpoint','cleanup','error','done','mass_fix')),
        summary     TEXT NOT NULL,
        detail      JSONB DEFAULT '{}',
        created     TIMESTAMPTZ NOT NULL DEFAULT NOW()
    );
    CREATE INDEX IF NOT EXISTS idx_journal_run ON mca.journal(run_id);
    CREATE INDEX IF NOT EXISTS idx_journal_task ON mca.journal(task_id);
    """,
]


def current_version(conn) -> int:
    """Get the current migration version from the database."""
    try:
        row = conn.execute(
            "SELECT COALESCE(MAX(version), -1) FROM mca.migrations"
        ).fetchone()
        return row[0] if row else -1
    except Exception:
        return -1


def run_migrations(conn) -> int:
    """Run pending migrations. Returns number of migrations applied."""
    applied = 0
    cur_version = current_version(conn)

    for i, sql in enumerate(MIGRATIONS):
        if i <= cur_version:
            continue
        conn.execute(sql)
        # Record migration (table may not exist for migration 0)
        if i > 0:
            conn.execute(
                "INSERT INTO mca.migrations (version) VALUES (%s) ON CONFLICT DO NOTHING",
                (i,),
            )
        applied += 1

    return applied
