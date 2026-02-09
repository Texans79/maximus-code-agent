"""JournalWriter — structured run journaling to DB + markdown."""
from __future__ import annotations

import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from mca.log import get_logger

log = get_logger("journal")


class JournalWriter:
    """Records structured journal entries for a single MCA run.

    Each entry goes to both the mca.journal table (for querying) and a
    markdown file (for human review).
    """

    def __init__(self, store, task_id: str | None, run_id: str,
                 workspace: Path, task_description: str = "") -> None:
        self._store = store
        self._task_id = task_id
        self._run_id = run_id
        self._workspace = workspace
        self._task_description = task_description
        self._seq = 0
        self._started = time.monotonic()
        self._start_time = datetime.now(timezone.utc)
        self._entries: list[dict[str, Any]] = []

    @property
    def run_id(self) -> str:
        return self._run_id

    def log(self, phase: str, summary: str, detail: dict | None = None) -> None:
        """Write a journal entry to DB and local buffer."""
        self._seq += 1
        entry = {
            "seq": self._seq,
            "phase": phase,
            "summary": summary,
            "detail": detail or {},
            "elapsed_s": round(time.monotonic() - self._started, 2),
        }
        self._entries.append(entry)

        if self._store:
            try:
                self._store.add_journal_entry(
                    task_id=self._task_id,
                    run_id=self._run_id,
                    seq=self._seq,
                    phase=phase,
                    summary=summary,
                    detail=detail,
                )
            except Exception as e:
                log.warning("Journal DB write failed: %s", e)

        log.debug("journal [%s] %s", phase, summary)

    def export_markdown(self) -> Path:
        """Write journal entries to a markdown file. Returns the file path."""
        journal_dir = self._workspace / ".mca" / "journal"
        journal_dir.mkdir(parents=True, exist_ok=True)
        path = journal_dir / f"{self._run_id[:8]}.md"

        lines = [
            f"# MCA Run Journal — {self._run_id[:8]}",
            f"Task: {self._task_description}",
            f"Started: {self._start_time.isoformat()}",
            "",
        ]

        current_phase = None
        for entry in self._entries:
            phase = entry["phase"]
            if phase != current_phase:
                lines.append(f"## {phase.title()}")
                current_phase = phase
            elapsed = entry["elapsed_s"]
            lines.append(f"- {entry['summary']} ({elapsed}s)")

        lines.append("")
        path.write_text("\n".join(lines))
        log.info("Journal exported to %s", path)
        return path

    def close(self) -> None:
        """Write final duration and export markdown."""
        duration = round(time.monotonic() - self._started, 1)
        self.log("done", f"Duration: {duration}s")
        try:
            self.export_markdown()
        except Exception as e:
            log.warning("Journal markdown export failed: %s", e)

    @property
    def entries(self) -> list[dict[str, Any]]:
        return list(self._entries)

    @property
    def duration_s(self) -> float:
        return round(time.monotonic() - self._started, 1)
