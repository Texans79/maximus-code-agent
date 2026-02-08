"""MCA CLI — all user-facing commands."""
from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer
from rich.panel import Panel
from rich.table import Table

from mca.log import console, setup_logging

app = typer.Typer(
    name="mca",
    help="Maximus Code Agent – local-first AI coding agent with safety rails.",
    no_args_is_help=True,
)

memory_app = typer.Typer(help="Long-term memory commands.")
app.add_typer(memory_app, name="memory")

tools_app = typer.Typer(help="Tool registry commands.")
app.add_typer(tools_app, name="tools")

llm_app = typer.Typer(help="LLM endpoint commands.")
app.add_typer(llm_app, name="llm")

telegram_app = typer.Typer(help="Telegram bot commands.")
app.add_typer(telegram_app, name="telegram")


def _resolve_workspace(workspace: str | None) -> Path:
    # If explicit path given, use it directly
    if workspace:
        ws = Path(workspace).resolve()
    else:
        from mca.config import load_config
        cfg = load_config(".")
        ws = Path(cfg.workspace).resolve()
    if not ws.exists():
        console.print(f"[error]Workspace not found: {ws}[/error]")
        raise typer.Exit(1)
    return ws


# ── mca run ──────────────────────────────────────────────────────────────────
@app.command()
def run(
    task: str = typer.Argument(..., help="Task description for the agent."),
    workspace: Optional[str] = typer.Option(None, "--workspace", "-w", help="Target workspace dir."),
    mode: Optional[str] = typer.Option(None, "--mode", "-m", help="Approval mode: auto|ask|paranoid."),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Run an AI coding task on a workspace."""
    setup_logging(log_dir=".mca/logs", verbose=verbose)
    from mca.config import load_config
    cfg = load_config(workspace or ".")
    ws = _resolve_workspace(workspace)
    approval = mode or cfg.approval_mode

    console.print(Panel(f"[bold]Task:[/bold] {task}\n[bold]Workspace:[/bold] {ws}\n[bold]Mode:[/bold] {approval}",
                        title="Maximus Code Agent", border_style="cyan"))

    from mca.orchestrator.loop import run_task
    result = run_task(task=task, workspace=ws, config=cfg, approval_mode=approval)

    if result.get("success"):
        console.print("[success]Task completed successfully.[/success]")
    else:
        console.print(f"[error]Task failed: {result.get('error', 'unknown')}[/error]")
        raise typer.Exit(1)


# ── mca status ───────────────────────────────────────────────────────────────
@app.command()
def status(verbose: bool = typer.Option(False, "--verbose", "-v")) -> None:
    """Show system telemetry: CPU, RAM, GPU, disk."""
    from mca.telemetry.collectors import collect_all
    data = collect_all()

    table = Table(title="System Status", show_header=True, header_style="bold cyan")
    table.add_column("Metric", style="bold")
    table.add_column("Value", justify="right")

    # CPU
    table.add_row("CPU", f"{data['cpu']['name']}")
    table.add_row("  Load (1m)", f"{data['cpu']['load_1m']:.1f}%")
    table.add_row("  Cores", f"{data['cpu']['cores_physical']}P / {data['cpu']['cores_logical']}L")

    # RAM
    ram = data["ram"]
    table.add_row("RAM", f"{ram['used_gb']:.1f} / {ram['total_gb']:.1f} GB ({ram['percent']:.0f}%)")

    # Disk
    for d in data["disks"]:
        table.add_row(f"Disk {d['mount']}", f"{d['used_gb']:.0f} / {d['total_gb']:.0f} GB ({d['percent']:.0f}%)")

    # GPU
    for gpu in data.get("gpus", []):
        table.add_row(f"GPU {gpu['index']}", gpu["name"])
        table.add_row("  Temp", f"{gpu['temp_c']}°C")
        table.add_row("  Util", f"{gpu['util_percent']}%")
        table.add_row("  VRAM", f"{gpu['mem_used_mb']} / {gpu['mem_total_mb']} MB")
        table.add_row("  Power", f"{gpu['power_w']}W")

    # NVMe
    for nv in data.get("nvme", []):
        table.add_row(f"NVMe {nv['device']}", f"{nv['temp_c']}°C")

    console.print(table)


# ── mca init ─────────────────────────────────────────────────────────────────
@app.command()
def init(
    template: str = typer.Option("python-cli", "--template", "-t",
                                  help="Template: python-cli|fastapi|node-ts|docker-service"),
    name: str = typer.Option("my-project", "--name", "-n", help="Project name."),
    dest: Optional[str] = typer.Option(None, "--dest", "-d", help="Destination dir."),
) -> None:
    """Scaffold a new project from a template."""
    from mca.templates.registry import create_from_template
    out = create_from_template(template, name, dest)
    console.print(f"[success]Created project '{name}' at {out}[/success]")


# ── mca rollback ─────────────────────────────────────────────────────────────
@app.command()
def rollback(
    workspace: Optional[str] = typer.Option(None, "--workspace", "-w"),
) -> None:
    """Rollback the last MCA checkpoint."""
    ws = _resolve_workspace(workspace)
    from mca.tools.git_ops import GitOps
    git = GitOps(ws)
    ref = git.rollback()
    if ref:
        console.print(f"[success]Rolled back to {ref}[/success]")
    else:
        console.print("[warn]No MCA checkpoint found to rollback.[/warn]")


# ── mca memory ───────────────────────────────────────────────────────────────
@memory_app.command("add")
def memory_add(
    content: str = typer.Argument(..., help="Content to remember."),
    tags: Optional[str] = typer.Option(None, "--tags", "-t", help="Comma-separated tags."),
    category: str = typer.Option("general", "--category", "-c",
                                  help="Category: general|decision|recipe|pattern|error|context"),
    workspace: Optional[str] = typer.Option(None, "--workspace", "-w"),
) -> None:
    """Store a knowledge entry in long-term memory (auto-embeds via Ollama)."""
    from mca.config import load_config
    from mca.memory.base import get_store
    cfg = load_config(workspace or ".")
    store = get_store(cfg)
    tag_list = [t.strip() for t in tags.split(",")] if tags else []

    # Auto-embed for vector similarity search
    embedding = None
    try:
        from mca.memory.embeddings import get_embedder
        emb = get_embedder(cfg)
        embedding = emb.embed(content)
        emb.close()
    except Exception as e:
        console.print(f"[dim]Embedding skipped: {e}[/dim]")

    mid = store.add(content=content, tags=tag_list, project=str(Path.cwd()),
                    category=category, embedding=embedding)
    console.print(f"[success]Stored memory {mid} ({store.backend_name})"
                  f"{' + embedding' if embedding else ''}[/success]")


@memory_app.command("search")
def memory_search(
    query: str = typer.Argument(..., help="Search query."),
    limit: int = typer.Option(5, "--limit", "-n"),
    workspace: Optional[str] = typer.Option(None, "--workspace", "-w"),
) -> None:
    """Search stored knowledge entries."""
    from mca.config import load_config
    from mca.memory.base import get_store
    cfg = load_config(workspace or ".")
    store = get_store(cfg)
    results = store.search(query=query, limit=limit)
    if not results:
        console.print(f"[dim]No results found. (backend: {store.backend_name})[/dim]")
        return
    console.print(f"[dim]{len(results)} result(s) from {store.backend_name}[/dim]")
    for r in results:
        console.print(Panel(
            f"{r['content']}\n[dim]category={r.get('category','general')} | "
            f"tags={r.get('tags',[])} | project={r.get('project','')} | {r.get('created','')}[/dim]",
            border_style="blue",
        ))


# ── mca telegram ─────────────────────────────────────────────────────────────
@telegram_app.command("start")
def telegram_start(
    workspace: Optional[str] = typer.Option(None, "--workspace", "-w"),
) -> None:
    """Start the Telegram bot."""
    from mca.config import load_config
    cfg = load_config(workspace or ".")
    token = cfg.telegram.token
    if not token:
        console.print("[error]Set MCA_TELEGRAM_TOKEN or telegram.token in config.[/error]")
        raise typer.Exit(1)
    from mca.telegram.bot import start_bot
    console.print("[info]Starting Telegram bot…[/info]")
    start_bot(cfg)


# ── mca embed ───────────────────────────────────────────────────────────────
@app.command()
def embed(
    text: str = typer.Argument(..., help="Text to embed."),
) -> None:
    """Generate an embedding vector for text via Ollama."""
    from mca.memory.embeddings import get_embedder
    emb = get_embedder()
    vec = emb.embed(text)
    emb.close()
    console.print(f"[bold]Model:[/bold] {emb.model}")
    console.print(f"[bold]Dimensions:[/bold] {len(vec)}")
    console.print(f"[bold]Sample:[/bold] [{vec[0]:.6f}, {vec[1]:.6f}, … {vec[-1]:.6f}]")


# ── mca llm ─────────────────────────────────────────────────────────────────
@llm_app.command("ping")
def llm_ping() -> None:
    """Verify the vLLM endpoint is reachable."""
    from mca.llm.client import get_client
    client = get_client()
    result = client.ping()
    client.close()
    if result["ok"]:
        console.print(f"[success]vLLM OK[/success] — {result['endpoint']}")
        for m in result["models"]:
            console.print(f"  [bold]{m}[/bold]")
    else:
        console.print(f"[error]vLLM unreachable: {result.get('error', 'unknown')}[/error]")
        raise typer.Exit(1)


# ── mca tools ───────────────────────────────────────────────────────────────
@tools_app.command("list")
def tools_list(
    workspace: Optional[str] = typer.Option(None, "--workspace", "-w"),
) -> None:
    """List all registered tools and their actions."""
    from mca.config import load_config
    from mca.tools.registry import build_registry
    cfg = load_config(workspace or ".")
    ws = _resolve_workspace(workspace)
    reg = build_registry(ws, cfg)
    tools = reg.list_tools()
    table = Table(title="Registered Tools", show_header=True, header_style="bold cyan")
    table.add_column("Tool", style="bold")
    table.add_column("Actions", justify="right")
    table.add_column("Description")
    for t in tools:
        table.add_row(t["name"], str(len(t["actions"])), t["description"])
    console.print(table)
    console.print(f"\n[dim]{sum(len(t['actions']) for t in tools)} total actions across {len(tools)} tools[/dim]")


@tools_app.command("verify")
def tools_verify(
    workspace: Optional[str] = typer.Option(None, "--workspace", "-w"),
) -> None:
    """Verify all tools are functional."""
    from mca.config import load_config
    from mca.tools.registry import build_registry
    cfg = load_config(workspace or ".")
    ws = _resolve_workspace(workspace)
    reg = build_registry(ws, cfg)
    results = reg.verify_all()
    for name, result in results.items():
        status = "[success]OK[/success]" if result.ok else f"[error]FAIL: {result.error}[/error]"
        console.print(f"  {name}: {status}")


# ── mca test ────────────────────────────────────────────────────────────────
@app.command("test")
def test_cmd(
    workspace: Optional[str] = typer.Option(None, "--workspace", "-w"),
) -> None:
    """Detect and run project tests."""
    from mca.config import load_config
    from mca.tools.registry import build_registry
    cfg = load_config(workspace or ".")
    ws = _resolve_workspace(workspace)
    reg = build_registry(ws, cfg)
    runner = reg.get_tool("test_runner")
    if not runner:
        console.print("[error]TestRunner tool not available[/error]")
        raise typer.Exit(1)
    result = runner.execute("run_tests", {})
    if result.ok:
        d = result.data
        console.print(f"[success]Tests passed[/success] — {d.get('framework', '?')}")
        console.print(f"  passed={d.get('passed', '?')} failed={d.get('failed', '?')}")
    else:
        console.print(f"[error]Tests failed: {result.error}[/error]")
        if result.data.get("stdout"):
            console.print(result.data["stdout"][-2000:])
        raise typer.Exit(1)


# ── mca memory recall ──────────────────────────────────────────────────────
@memory_app.command("recall")
def memory_recall(
    query: str = typer.Argument(..., help="Task or query to find similar past work."),
    limit: int = typer.Option(5, "--limit", "-n"),
    workspace: Optional[str] = typer.Option(None, "--workspace", "-w"),
) -> None:
    """Recall similar past tasks/knowledge via pgvector."""
    from mca.config import load_config
    from mca.memory.base import get_store
    from mca.memory.recall import recall_similar
    from mca.memory.embeddings import get_embedder
    cfg = load_config(workspace or ".")
    store = get_store(cfg)
    embedder = get_embedder(cfg)
    results = recall_similar(store, embedder, query, limit=limit)
    embedder.close()
    if not results:
        console.print("[dim]No similar entries found.[/dim]")
        return
    console.print(f"[bold]{len(results)} similar entries[/bold] (backend: {store.backend_name})")
    for r in results:
        sim = r.get("similarity", 0)
        console.print(Panel(
            f"{r['content']}\n[dim]similarity={sim:.4f} | category={r.get('category','general')} | "
            f"tags={r.get('tags',[])}[/dim]",
            border_style="green" if sim > 0.7 else "yellow",
        ))


if __name__ == "__main__":
    app()
