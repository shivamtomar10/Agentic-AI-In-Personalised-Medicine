"""Outbound email. The prototype has no SMTP server, so messages are written to logs/outbox.log and the
server console. To send real email, replace `OutboxMailer.send` with an SMTP or API call; nothing else changes.
"""
from __future__ import annotations

import logging
import threading
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Deque, Dict, Optional

log = logging.getLogger("caretrace.mail")


class OutboxMailer:
    def __init__(self, path: Optional[Path] = None):
        self.path = Path(path) if path else None
        self._lock = threading.Lock()
        self.sent: Deque[Dict[str, str]] = deque(maxlen=50)   # inspected by tests

    def send(self, to: str, subject: str, body: str) -> None:
        msg = {"to": to, "subject": subject, "body": body, "at": datetime.now(timezone.utc).isoformat()}
        with self._lock:
            self.sent.append(msg)
            if self.path:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with open(self.path, "a", encoding="utf-8", newline="\n") as f:
                    f.write(f"--- {msg['at']}\nTo: {to}\nSubject: {subject}\n\n{body}\n\n")
        log.info("Email queued to %s: %s (see outbox.log)", to, subject)
        print(f"[mail] To: {to} | {subject}\n{body}\n", flush=True)
