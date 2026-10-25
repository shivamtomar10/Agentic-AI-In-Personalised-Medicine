"""Tamper-evident audit trail (AGENTS.md Rule 5): append-only JSONL, each record hash-chained to the previous."""
from __future__ import annotations

import hashlib
import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.config import get_settings


class AuditLogger:
    def __init__(self, log_path: Optional[str] = None):
        self.path = Path(log_path) if log_path else get_settings().logs_dir / "audit_trail.jsonl"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._last = self._read_last_hash()

    def _read_last_hash(self) -> str:
        if not self.path.exists():
            return "GENESIS"
        last = "GENESIS"
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                last = json.loads(line)["hash"]
        return last

    def _tail_hash(self) -> str:
        """Hash of the last record ON DISK. Re-read on every write so a second process (e.g. a second server
        worker) appending to the same file cannot fork the chain from a stale in-memory value."""
        if not self.path.exists() or self.path.stat().st_size == 0:
            return "GENESIS"
        with open(self.path, "rb") as f:
            f.seek(0, 2)
            size, block, data = f.tell(), 4096, b""
            while size > 0 and data.count(b"\n") < 2:
                step = min(block, size)
                size -= step
                f.seek(size)
                data = f.read(step) + data
        lines = [l for l in data.splitlines() if l.strip()]
        return json.loads(lines[-1])["hash"] if lines else "GENESIS"

    @staticmethod
    def _digest(rec: Dict[str, Any]) -> str:
        body = {k: v for k, v in rec.items() if k != "hash"}
        return hashlib.sha256(json.dumps(body, sort_keys=True, default=str, separators=(",", ":")).encode()).hexdigest()

    def log_event(self, event_type: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        with self._lock:
            self._last = self._tail_hash()
            rec = {"timestamp": datetime.now(timezone.utc).isoformat(), "event": event_type, "payload": payload, "prev_hash": self._last}
            rec["hash"] = self._digest(rec)
            with open(self.path, "a", encoding="utf-8", newline="\n") as f:
                f.write(json.dumps(rec, default=str) + "\n")
            self._last = rec["hash"]
            return rec

    def read_all(self) -> List[Dict[str, Any]]:
        if not self.path.exists():
            return []
        return [json.loads(l) for l in self.path.read_text(encoding="utf-8").splitlines() if l.strip()]

    def verify_chain(self) -> bool:
        prev = "GENESIS"
        for rec in self.read_all():
            if rec["prev_hash"] != prev or rec["hash"] != self._digest(rec):
                return False
            prev = rec["hash"]
        return True


class NullAudit:
    """Explicit no-op sink for evaluation runs (thousands of runs; audit is tested separately)."""
    def log_event(self, *a, **k):
        return {}
