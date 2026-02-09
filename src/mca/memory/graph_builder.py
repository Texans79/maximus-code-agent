"""Graph builder — AST parsing, file walking, node/edge extraction.

Walks a workspace, parses Python files with the ast module,
JS/TS files with regex, and dependency manifests via RepoIndexer.
Produces GraphData (nodes + edges) ready for insertion into PostgreSQL.
"""
from __future__ import annotations

import ast
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from mca.log import get_logger

log = get_logger("graph.builder")

SKIP_DIRS = {
    "node_modules", "__pycache__", "venv", ".venv", ".git", ".tox",
    ".mypy_cache", ".pytest_cache", ".ruff_cache", "dist", "build",
    ".eggs", "egg-info",
}


@dataclass
class GraphNode:
    """A node to be inserted into graph_nodes."""
    node_type: str  # file, function, class, module, dependency
    name: str
    file_path: str | None = None
    line_number: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class GraphEdge:
    """An edge to be inserted into graph_edges."""
    source: GraphNode
    target: GraphNode
    edge_type: str  # imports, calls, extends, contains, defines, depends_on
    weight: float = 1.0
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class GraphData:
    """Complete extraction result for a workspace."""
    nodes: list[GraphNode] = field(default_factory=list)
    edges: list[GraphEdge] = field(default_factory=list)


# ── File walker ───────────────────────────────────────────────────────────


def walk_workspace(workspace: Path, max_depth: int = 10) -> list[Path]:
    """Walk workspace files, skipping irrelevant directories.

    Returns sorted list of relative Paths.
    """
    files: list[Path] = []
    ws = str(workspace)
    for root, dirs, filenames in os.walk(workspace):
        depth = root.replace(ws, "").count(os.sep)
        if depth >= max_depth:
            dirs.clear()
            continue
        dirs[:] = [
            d for d in dirs
            if d not in SKIP_DIRS and not d.startswith(".") and not d.endswith(".egg-info")
        ]
        for f in filenames:
            full = Path(root) / f
            files.append(full.relative_to(workspace))
    return sorted(files)


# ── Python AST extractor ─────────────────────────────────────────────────


def extract_python(file_path: Path, source: str) -> GraphData:
    """Parse a Python file with ast and extract nodes + edges.

    Extracts: file, function, class, module nodes; imports, contains,
    extends, calls edges.
    """
    data = GraphData()
    rel = str(file_path)

    try:
        tree = ast.parse(source, filename=rel)
    except SyntaxError:
        log.debug("Skipping %s: syntax error", rel)
        return data

    file_node = GraphNode(node_type="file", name=rel, file_path=rel)
    data.nodes.append(file_node)

    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                mod = GraphNode(node_type="module", name=alias.name)
                data.nodes.append(mod)
                data.edges.append(GraphEdge(source=file_node, target=mod, edge_type="imports"))

        elif isinstance(node, ast.ImportFrom) and node.module:
            mod = GraphNode(node_type="module", name=node.module)
            data.nodes.append(mod)
            data.edges.append(GraphEdge(
                source=file_node, target=mod, edge_type="imports",
                metadata={"names": [a.name for a in (node.names or [])]},
            ))

        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            func = GraphNode(
                node_type="function", name=node.name,
                file_path=rel, line_number=node.lineno,
                metadata={"args": [a.arg for a in node.args.args]},
            )
            data.nodes.append(func)
            data.edges.append(GraphEdge(source=file_node, target=func, edge_type="contains"))
            _extract_calls(func, node, data)

        elif isinstance(node, ast.ClassDef):
            cls = GraphNode(
                node_type="class", name=node.name,
                file_path=rel, line_number=node.lineno,
            )
            data.nodes.append(cls)
            data.edges.append(GraphEdge(source=file_node, target=cls, edge_type="contains"))

            # Base classes → extends
            for base in node.bases:
                base_name = _resolve_name(base)
                if base_name:
                    base_node = GraphNode(node_type="class", name=base_name)
                    data.nodes.append(base_node)
                    data.edges.append(GraphEdge(source=cls, target=base_node, edge_type="extends"))

            # Methods
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    method = GraphNode(
                        node_type="function", name=f"{node.name}.{item.name}",
                        file_path=rel, line_number=item.lineno,
                        metadata={"class": node.name, "args": [a.arg for a in item.args.args]},
                    )
                    data.nodes.append(method)
                    data.edges.append(GraphEdge(source=cls, target=method, edge_type="contains"))
                    _extract_calls(method, item, data)

    return data


def _extract_calls(func_node: GraphNode, ast_node: ast.AST, data: GraphData) -> None:
    """Extract function call edges from within a function body."""
    for child in ast.walk(ast_node):
        if isinstance(child, ast.Call) and isinstance(child.func, ast.Name):
            target = GraphNode(node_type="function", name=child.func.id)
            data.nodes.append(target)
            data.edges.append(GraphEdge(source=func_node, target=target, edge_type="calls"))


def _resolve_name(node: ast.expr) -> str | None:
    """Resolve an AST expression to a simple name string."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


# ── JS/TS regex extractor ────────────────────────────────────────────────

_JS_IMPORT_RE = re.compile(
    r"""(?:import\s+(?:(?:\{[^}]*\}|\*\s+as\s+\w+|\w+)\s+from\s+)?['"]([^'"]+)['"]"""
    r"""|require\s*\(\s*['"]([^'"]+)['"]\s*\))""",
    re.MULTILINE,
)
_JS_EXPORT_RE = re.compile(
    r"""export\s+(?:default\s+)?(?:function|class|const|let|var)\s+(\w+)""",
    re.MULTILINE,
)


def extract_js_ts(file_path: Path, source: str) -> GraphData:
    """Extract imports and exports from JS/TS files using regex."""
    data = GraphData()
    rel = str(file_path)

    file_node = GraphNode(node_type="file", name=rel, file_path=rel)
    data.nodes.append(file_node)

    for match in _JS_IMPORT_RE.finditer(source):
        module_name = match.group(1) or match.group(2)
        if module_name:
            mod = GraphNode(node_type="module", name=module_name)
            data.nodes.append(mod)
            data.edges.append(GraphEdge(source=file_node, target=mod, edge_type="imports"))

    for match in _JS_EXPORT_RE.finditer(source):
        name = match.group(1)
        line = source[:match.start()].count("\n") + 1
        export_node = GraphNode(
            node_type="function", name=name,
            file_path=rel, line_number=line,
        )
        data.nodes.append(export_node)
        data.edges.append(GraphEdge(source=file_node, target=export_node, edge_type="contains"))

    return data


# ── Dependency extractor ─────────────────────────────────────────────────


def extract_dependencies(workspace: Path) -> GraphData:
    """Extract dependency nodes using RepoIndexer._parse_deps()."""
    from mca.tools.repo_indexer import RepoIndexer

    data = GraphData()
    indexer = RepoIndexer(workspace)
    deps = indexer._parse_deps()

    for manifest, parsed in deps.items():
        manifest_node = GraphNode(node_type="file", name=manifest, file_path=manifest)
        data.nodes.append(manifest_node)

        dep_list: list[str] = []
        if isinstance(parsed, list):
            dep_list = parsed
        elif isinstance(parsed, dict):
            for _category, items in parsed.items():
                if isinstance(items, list):
                    dep_list.extend(items)

        for dep_name in dep_list:
            dep_node = GraphNode(
                node_type="dependency", name=dep_name,
                metadata={"manifest": manifest},
            )
            data.nodes.append(dep_node)
            data.edges.append(GraphEdge(
                source=manifest_node, target=dep_node, edge_type="depends_on",
            ))

    return data


# ── Master build ─────────────────────────────────────────────────────────


def build_graph(workspace: Path) -> GraphData:
    """Full graph extraction pipeline for a workspace."""
    data = GraphData()
    ws = workspace.resolve()
    files = walk_workspace(ws)
    log.info("Walking %d files in %s", len(files), ws)

    py_count = js_count = 0

    for rel_path in files:
        full_path = ws / rel_path
        suffix = rel_path.suffix.lower()

        if suffix == ".py":
            try:
                source = full_path.read_text(errors="ignore")
                file_data = extract_python(rel_path, source)
                data.nodes.extend(file_data.nodes)
                data.edges.extend(file_data.edges)
                py_count += 1
            except Exception as e:
                log.debug("Failed to parse %s: %s", rel_path, e)

        elif suffix in (".js", ".ts", ".jsx", ".tsx"):
            try:
                source = full_path.read_text(errors="ignore")
                file_data = extract_js_ts(rel_path, source)
                data.nodes.extend(file_data.nodes)
                data.edges.extend(file_data.edges)
                js_count += 1
            except Exception as e:
                log.debug("Failed to parse %s: %s", rel_path, e)

        else:
            data.nodes.append(GraphNode(
                node_type="file", name=str(rel_path), file_path=str(rel_path),
            ))

    # Dependencies from manifests
    try:
        dep_data = extract_dependencies(ws)
        data.nodes.extend(dep_data.nodes)
        data.edges.extend(dep_data.edges)
    except Exception as e:
        log.debug("Dependency extraction failed: %s", e)

    log.info("Extracted %d nodes, %d edges (py=%d, js=%d)",
             len(data.nodes), len(data.edges), py_count, js_count)
    return data
