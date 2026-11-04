"""Presence discovery ONLY: no CLI execution, auth/profile reads or model calls."""

import os
from pathlib import Path
import threading

AGENTS = (("pi", "Pi", "sdk-jsonl-probe"),)
WINDOWS = os.name == "nt"


def path_directories():
    raw = os.environ.get("PATH", "")
    entries = raw.split(os.pathsep)
    if len(raw) > 65536 or len(entries) > 256 or "\0" in raw:
        raise ValueError("Oversized/invalid PATH; discovery not performed")
    directories, ignored = [], 0
    cwd = os.path.normcase(os.path.abspath(os.getcwd()))
    seen = set()
    for entry in entries:
        directory = Path(entry.strip('"'))
        if (
            not entry
            or not directory.is_absolute()
            or str(directory).startswith(("\\\\", "//"))
        ):
            ignored += 1
            continue
        key = os.path.normcase(os.path.abspath(directory))
        if key == cwd:
            ignored += 1
            continue
        if key not in seen:
            seen.add(key)
            directories.append(directory)
    return directories, ignored


def scan_path():
    directories, ignored = path_directories()
    agents, unreadable = [], 0
    suffixes = (".exe", ".com", ".cmd", ".bat") if WINDOWS else ("",)
    for name, label, protocol in AGENTS:
        found = None
        for directory in directories:
            for suffix in suffixes:
                candidate = directory / (name + suffix)
                try:
                    if candidate.is_file() and (
                        WINDOWS or os.access(candidate, os.X_OK)
                    ):
                        found = str(candidate)
                        break
                except OSError:
                    unreadable += 1
            if found is not None:
                break
        agents.append(
            {
                "id": name,
                "label": label,
                "documented_protocol": protocol,
                "status": "detected" if found else "not_found_in_scanned_path",
                "path": found,
                "runtime_verified": False,
                "connected": False,
            }
        )
    return {
        "agents": agents,
        "scope": "absolute_PATH_only_excluding_cwd_and_UNC",
        "ignored_entries": ignored,
        "unreadable_candidates": unreadable,
    }


class Discovery:
    """One owned read-only scanner; slow PATH metadata cannot block serial HTTP."""

    def __init__(self):
        self.guard = threading.RLock()
        self.thread = None
        self.closed = False
        self.generation = 0
        self.status = "idle"
        self.result = {
            "agents": [],
            "scope": "not_scanned",
            "ignored_entries": 0,
            "unreadable_candidates": 0,
        }
        self.error = ""

    def snapshot(self):
        with self.guard:
            return {
                **self.result,
                "agents": [item.copy() for item in self.result["agents"]],
                "status": self.status,
                "generation": self.generation,
                "connected": False,
                "runtime_verified": False,
                "executed": False,
                "model_called": False,
                "error": self.error,
            }

    def start(self):
        with self.guard:
            if self.closed:
                raise ValueError("Agent discovery is closed")
            if self.thread is not None and self.thread.is_alive():
                return self.snapshot()
            self.generation += 1
            self.status, self.error = "discovering", ""
            self.result = {
                "agents": [],
                "scope": "not_scanned",
                "ignored_entries": 0,
                "unreadable_candidates": 0,
            }
            # Only file-presence metadata; no child process, document or paid work.
            self.thread = threading.Thread(
                target=self._run, name="HWP-agent-presence", daemon=True
            )
            try:
                self.thread.start()
            except RuntimeError:
                self.status, self.error = (
                    "failed",
                    "탐색을 시작하지 못했습니다. 자동 재시도하지 않습니다.",
                )
            return self.snapshot()

    def _run(self):
        try:
            result = scan_path()
            with self.guard:
                if not self.closed:
                    self.result, self.status = result, "finished"
        except Exception:
            with self.guard:
                if not self.closed:
                    self.status, self.error = (
                        "failed",
                        "PATH 탐색을 완료하지 못했습니다. 전체 설치/인증 상태는 알 수 없습니다. 자동 재시도하지 않습니다.",
                    )

    def close(self):
        with self.guard:
            self.closed, self.status = True, "closed"
