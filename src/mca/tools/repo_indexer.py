"""RepoIndexer — map directory structure, find entrypoints, parse dependencies."""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from mca.tools.base import ToolBase, ToolResult

ENTRYPOINT_PATTERNS = [
    "main.py", "app.py", "index.py", "manage.py", "cli.py",
    "index.js", "index.ts", "app.js", "app.ts", "server.js", "server.ts",
    "main.go", "main.rs", "lib.rs",
    "Makefile", "Dockerfile", "docker-compose.yml", "docker-compose.yaml",
]

_SKIP_DIRS = {"node_modules", "__pycache__", "venv", ".venv", ".git", ".tox", ".mypy_cache"}


class RepoIndexer(ToolBase):
    """Map repo structure, detect entrypoints, parse dependency manifests."""

    def __init__(self, workspace: Path) -> None:
        self._ws = workspace

    @property
    def name(self) -> str:
        return "repo_indexer"

    @property
    def description(self) -> str:
        return "Map directory structure, find entrypoints, parse dependency manifests"

    def actions(self) -> dict[str, str]:
        return {
            "index_repo": "Full repo index: structure, entrypoints, dependencies",
            "find_entrypoints": "Find main entrypoint files",
            "parse_dependencies": "Parse dependency manifests",
        }

    def _find_entrypoints(self) -> list[str]:
        found = []
        for pattern in ENTRYPOINT_PATTERNS:
            for match in self._ws.glob(f"**/{pattern}"):
                rel = str(match.relative_to(self._ws))
                if any(p in _SKIP_DIRS or p.startswith(".") for p in Path(rel).parts):
                    continue
                found.append(rel)
        return sorted(set(found))

    def _parse_requirements(self, path: Path) -> list[str]:
        deps = []
        for line in path.read_text(errors="ignore").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and not line.startswith("-"):
                deps.append(re.split(r"[>=<!\[]", line)[0].strip())
        return deps

    def _parse_package_json(self, path: Path) -> dict:
        data = json.loads(path.read_text())
        return {
            "dependencies": list(data.get("dependencies", {}).keys()),
            "devDependencies": list(data.get("devDependencies", {}).keys()),
        }

    def _parse_pyproject(self, path: Path) -> list[str]:
        text = path.read_text(errors="ignore")
        deps = []
        in_deps = False
        for line in text.splitlines():
            if re.match(r"^dependencies\s*=\s*\[", line):
                in_deps = True
                continue
            if in_deps:
                if line.strip().startswith("]"):
                    break
                m = re.search(r'"([^"]+)"', line)
                if m:
                    deps.append(re.split(r"[>=<!\[]", m.group(1))[0].strip())
        return deps

    def _parse_gomod(self, path: Path) -> list[str]:
        deps = []
        in_require = False
        for line in path.read_text(errors="ignore").splitlines():
            if line.strip().startswith("require ("):
                in_require = True
                continue
            if in_require:
                if line.strip() == ")":
                    break
                parts = line.strip().split()
                if parts:
                    deps.append(parts[0])
        return deps

    def _parse_deps(self) -> dict[str, Any]:
        result: dict[str, Any] = {}
        checks = [
            ("requirements.txt", self._parse_requirements),
            ("pyproject.toml", self._parse_pyproject),
            ("package.json", self._parse_package_json),
            ("go.mod", self._parse_gomod),
            ("Cargo.toml", lambda p: "detected"),
            ("Gemfile", lambda p: "detected"),
        ]
        for filename, parser in checks:
            path = self._ws / filename
            if path.exists():
                try:
                    result[filename] = parser(path)
                except Exception as e:
                    result[filename] = f"parse error: {e}"
        return result

    def _file_type_counts(self, max_depth: int = 3) -> dict[str, int]:
        counts: dict[str, int] = {}
        for root, dirs, files in os.walk(self._ws):
            depth = str(root).replace(str(self._ws), "").count(os.sep)
            if depth >= max_depth:
                dirs.clear()
                continue
            dirs[:] = [d for d in dirs if d not in _SKIP_DIRS and not d.startswith(".")]
            for f in files:
                ext = Path(f).suffix or "(no ext)"
                counts[ext] = counts.get(ext, 0) + 1
        return dict(sorted(counts.items(), key=lambda x: -x[1]))

    def execute(self, action: str, args: dict[str, Any]) -> ToolResult:
        if action == "find_entrypoints":
            return ToolResult(ok=True, data={"entrypoints": self._find_entrypoints()})
        if action == "parse_dependencies":
            return ToolResult(ok=True, data={"dependencies": self._parse_deps()})
        if action == "index_repo":
            return ToolResult(ok=True, data={
                "entrypoints": self._find_entrypoints(),
                "dependencies": self._parse_deps(),
                "file_types": self._file_type_counts(),
            })
        raise ValueError(f"Unknown repo_indexer action: {action}")

    def verify(self) -> ToolResult:
        return ToolResult(ok=self._ws.is_dir(), data={
            "tool": "repo_indexer", "workspace": str(self._ws)})
