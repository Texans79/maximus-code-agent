"""Template registry — scaffold new projects from built-in templates."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from mca.log import get_logger

log = get_logger("templates")

TEMPLATES: dict[str, dict[str, Any]] = {
    "python-cli": {
        "description": "Python CLI project with Typer, pytest, pyproject.toml",
        "files": {
            "pyproject.toml": '''\
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "{name}"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = ["typer[all]>=0.9"]

[project.scripts]
{name} = "{name_under}.cli:app"

[tool.pytest.ini_options]
testpaths = ["tests"]
''',
            "src/{name_under}/__init__.py": '"""{ name } — a Python CLI."""\n__version__ = "0.1.0"\n',
            "src/{name_under}/cli.py": '''\
"""CLI entry point."""
import typer

app = typer.Typer(help="{name} CLI")


@app.command()
def hello(name: str = "world") -> None:
    """Say hello."""
    print(f"Hello, {{name}}!")


if __name__ == "__main__":
    app()
''',
            "tests/__init__.py": "",
            "tests/test_cli.py": '''\
"""Tests for {name} CLI."""
from typer.testing import CliRunner
from {name_under}.cli import app

runner = CliRunner()


def test_hello():
    result = runner.invoke(app, ["--name", "test"])
    assert result.exit_code == 0
    assert "Hello, test!" in result.output
''',
            "README.md": "# {name}\n\nA Python CLI project.\n\n## Install\n\n```bash\npip install -e .\n```\n\n## Usage\n\n```bash\n{name} hello --name World\n```\n",
            ".gitignore": "__pycache__/\n*.pyc\n*.egg-info/\ndist/\n.venv/\n",
        },
    },

    "fastapi": {
        "description": "FastAPI web service with uvicorn, pytest, Docker",
        "files": {
            "pyproject.toml": '''\
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "{name}"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = ["fastapi>=0.100", "uvicorn[standard]>=0.20"]

[tool.pytest.ini_options]
testpaths = ["tests"]
''',
            "src/{name_under}/__init__.py": "",
            "src/{name_under}/main.py": '''\
"""FastAPI application."""
from fastapi import FastAPI

app = FastAPI(title="{name}")


@app.get("/")
async def root():
    return {{"message": "Hello from {name}"}}


@app.get("/health")
async def health():
    return {{"status": "ok"}}
''',
            "tests/__init__.py": "",
            "tests/test_main.py": '''\
"""Tests for {name} API."""
from fastapi.testclient import TestClient
from {name_under}.main import app

client = TestClient(app)


def test_root():
    r = client.get("/")
    assert r.status_code == 200
    assert "message" in r.json()


def test_health():
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"
''',
            "Dockerfile": '''\
FROM python:3.13-slim
WORKDIR /app
COPY pyproject.toml .
COPY src/ src/
RUN pip install --no-cache-dir .
EXPOSE 8000
CMD ["uvicorn", "{name_under}.main:app", "--host", "0.0.0.0", "--port", "8000"]
''',
            "README.md": "# {name}\n\nA FastAPI web service.\n\n## Run\n\n```bash\nuvicorn {name_under}.main:app --reload\n```\n",
            ".gitignore": "__pycache__/\n*.pyc\n*.egg-info/\ndist/\n.venv/\n",
        },
    },

    "node-ts": {
        "description": "Node.js TypeScript project with Jest",
        "files": {
            "package.json": '''\
{{
  "name": "{name}",
  "version": "0.1.0",
  "scripts": {{
    "build": "tsc",
    "start": "node dist/index.js",
    "dev": "ts-node src/index.ts",
    "test": "jest"
  }},
  "devDependencies": {{
    "typescript": "^5.0",
    "@types/node": "^20",
    "jest": "^29",
    "ts-jest": "^29",
    "@types/jest": "^29",
    "ts-node": "^10"
  }}
}}
''',
            "tsconfig.json": '''\
{{
  "compilerOptions": {{
    "target": "ES2022",
    "module": "commonjs",
    "outDir": "dist",
    "rootDir": "src",
    "strict": true,
    "esModuleInterop": true,
    "declaration": true
  }},
  "include": ["src/**/*"]
}}
''',
            "src/index.ts": '''\
export function greet(name: string): string {{
  return `Hello, ${{name}}!`;
}}

if (require.main === module) {{
  console.log(greet("world"));
}}
''',
            "src/__tests__/index.test.ts": '''\
import {{ greet }} from "../index";

describe("greet", () => {{
  it("should greet by name", () => {{
    expect(greet("test")).toBe("Hello, test!");
  }});
}});
''',
            "jest.config.js": "module.exports = { preset: 'ts-jest', testEnvironment: 'node' };\n",
            "README.md": "# {name}\n\nA TypeScript Node.js project.\n\n## Setup\n\n```bash\nnpm install\nnpm run build\nnpm start\n```\n",
            ".gitignore": "node_modules/\ndist/\n*.js.map\n",
        },
    },

    "docker-service": {
        "description": "Docker service with docker-compose, healthcheck, env config",
        "files": {
            "Dockerfile": '''\
FROM python:3.13-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s CMD curl -f http://localhost:8080/health || exit 1
CMD ["python", "app.py"]
''',
            "docker-compose.yml": '''\
services:
  {name_under}:
    build: .
    ports:
      - "8080:8080"
    environment:
      - APP_ENV=production
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8080/health"]
      interval: 30s
      timeout: 5s
      retries: 3
''',
            "app.py": '''\
"""Simple HTTP service."""
from http.server import HTTPServer, BaseHTTPRequestHandler
import json
import os


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/health":
            self._respond(200, {{"status": "ok"}})
        elif self.path == "/":
            self._respond(200, {{"service": "{name}", "env": os.getenv("APP_ENV", "dev")}})
        else:
            self._respond(404, {{"error": "not found"}})

    def _respond(self, code, data):
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(data).encode())


if __name__ == "__main__":
    server = HTTPServer(("0.0.0.0", 8080), Handler)
    print("Serving on :8080")
    server.serve_forever()
''',
            "requirements.txt": "# Add dependencies here\n",
            "README.md": "# {name}\n\nA Docker service.\n\n## Run\n\n```bash\ndocker compose up --build\n```\n",
            ".gitignore": "__pycache__/\n*.pyc\n.env\n",
        },
    },
}


def create_from_template(template: str, name: str, dest: str | None = None) -> Path:
    """Create a project from a template."""
    if template not in TEMPLATES:
        available = ", ".join(TEMPLATES.keys())
        raise ValueError(f"Unknown template '{template}'. Available: {available}")

    tpl = TEMPLATES[template]
    name_under = name.replace("-", "_").replace(" ", "_")
    dest_path = Path(dest or name).resolve()
    dest_path.mkdir(parents=True, exist_ok=True)

    for rel_path_tpl, content_tpl in tpl["files"].items():
        rel_path = rel_path_tpl.format(name=name, name_under=name_under)
        content = content_tpl.format(name=name, name_under=name_under)
        full_path = dest_path / rel_path
        full_path.parent.mkdir(parents=True, exist_ok=True)
        full_path.write_text(content, encoding="utf-8")
        log.debug("wrote %s", full_path)

    log.info("created %s project '%s' at %s", template, name, dest_path)
    return dest_path
