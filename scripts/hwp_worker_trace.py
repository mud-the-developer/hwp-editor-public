"""Bounded owned-worker timing diagnostics. NOT document verification/approval."""

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import time

from hwp_security import _CacheMutationBlocked


class WorkerTrace:
    def __init__(self, workspace, phase):
        workspace = Path(workspace).absolute()
        if (
            type(phase) is not int
            or phase not in (0, 1, 2)
            or workspace.name != "_review"
            or any(
                p.is_symlink() or (hasattr(p, "is_junction") and p.is_junction())
                for p in (workspace, *workspace.parents)
            )
            or not workspace.is_dir()
            or not (workspace / "preparing.json").is_file()
            or (workspace / "manifest.json").exists()
        ):
            raise ValueError("Worker trace requires a real owned unprepared workspace")
        self.path = workspace / f"native-worker-timing-{phase}.jsonl"
        if self.path.is_symlink() or self.path.exists():
            raise FileExistsError("Existing worker trace retained; no reuse/retry")
        self.phase = phase
        self.started = time.monotonic()
        self.failed = False
        with self.path.open("x", encoding="utf-8"):
            pass
        self.event("entry")

    def event(self, stage):
        if not isinstance(stage, str) or not stage or len(stage) > 80:
            raise ValueError("Bounded worker stage name required")
        if self.failed:
            return
        try:
            if (
                self.path.is_symlink()
                or not self.path.is_file()
                or self.path.stat().st_size > 32768
            ):
                self.failed = True
                return
            data = {
                "diagnostic_only": True,
                "phase": self.phase,
                "pid": os.getpid(),
                "stage": stage,
                "elapsed_seconds": round(time.monotonic() - self.started, 6),
                "utc": datetime.now(timezone.utc).isoformat(),
            }
            with self.path.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(data, ensure_ascii=True) + "\n")
                stream.flush()
                os.fsync(stream.fileno())
        except (OSError, _CacheMutationBlocked):
            self.failed = True  # Diagnostics must NEVER prevent Clear/Quit/COM cleanup.
