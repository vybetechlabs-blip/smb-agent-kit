"""Delivery: senders and the send log.

`DryRunSender` writes each message to an outbox folder; `ResendSender` needs RESEND_API_KEY and
is never exercised by tests. `SendLog` records one row per (item id, draft version): a second
send of a logged key is refused with an error, never skipped silently. A sender is only ever
called by the approval queue, after the approval gate and the send-log check.
"""

from __future__ import annotations

import json
import os
import sqlite3
import urllib.request
from pathlib import Path
from typing import Protocol

SEND_LOG_SCHEMA = """
CREATE TABLE IF NOT EXISTS send_log (
    item_id TEXT NOT NULL,
    version INTEGER NOT NULL,
    sent_at TEXT NOT NULL,
    sender TEXT NOT NULL,
    ref TEXT,
    PRIMARY KEY (item_id, version)
);
"""


class Sender(Protocol):
    name: str

    def send(self, *, key: str, to: str, sender: str, subject: str, body: str) -> str:
        """Deliver one message and return a reference to it."""
        ...


class SenderUnavailable(RuntimeError):
    pass


def send_key(item_id: str, version: int) -> str:
    return f"{item_id}-v{version}"


class DryRunSender:
    """Writes `<outbox>/<key>.eml` instead of sending. The project chooses the outbox folder."""

    name = "dry-run"

    def __init__(self, outbox: Path, key_header: str = "X-Send-Key"):
        self.outbox = Path(outbox)
        self.key_header = key_header

    def send(self, *, key: str, to: str, sender: str, subject: str, body: str) -> str:
        self.outbox.mkdir(parents=True, exist_ok=True)
        path = self.outbox / f"{key}.eml"
        path.write_text(
            f"From: {sender}\nTo: {to}\nSubject: {subject}\n{self.key_header}: {key}\n\n{body}\n",
            encoding="utf-8",
        )
        return str(path)


class ResendSender:
    """Resend HTTP API. Never exercised by the test suite; needs RESEND_API_KEY to construct."""

    name = "resend"
    endpoint = "https://api.resend.com/emails"

    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or os.environ.get("RESEND_API_KEY")
        if not self.api_key:
            raise SenderUnavailable("RESEND_API_KEY is not set")

    def send(self, *, key: str, to: str, sender: str, subject: str, body: str) -> str:
        payload = {"from": sender, "to": [to], "subject": subject, "text": body}
        req = urllib.request.Request(
            self.endpoint,
            data=json.dumps(payload).encode(),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read()).get("id", "")


def make_sender(name: str, *, outbox: Path | None = None, key_header: str = "X-Send-Key") -> Sender:
    if name == "dry-run":
        if outbox is None:
            raise ValueError("dry-run needs an outbox folder")
        return DryRunSender(outbox, key_header=key_header)
    if name == "resend":
        return ResendSender()
    raise ValueError(f"unknown sender: {name}")


class SendLog:
    """One row per (item id, version) on the approval queue's own SQLite connection.

    The queue writes the row inside the same write transaction as the send, so two concurrent
    sends of one key cannot both get through (primary key plus write lock).
    """

    def __init__(self, db: sqlite3.Connection):
        self.db = db
        self.db.executescript(SEND_LOG_SCHEMA)

    def lookup(self, item_id: str, version: int) -> sqlite3.Row | None:
        return self.db.execute(
            "SELECT * FROM send_log WHERE item_id = ? AND version = ?", (item_id, version)
        ).fetchone()

    def history(self, item_id: str) -> list[sqlite3.Row]:
        return self.db.execute(
            "SELECT * FROM send_log WHERE item_id = ? ORDER BY version", (item_id,)
        ).fetchall()

    def record(self, item_id: str, version: int, sent_at: str, sender: str) -> None:
        self.db.execute(
            "INSERT INTO send_log (item_id, version, sent_at, sender) VALUES (?, ?, ?, ?)",
            (item_id, version, sent_at, sender),
        )

    def set_ref(self, item_id: str, version: int, ref: str) -> None:
        self.db.execute(
            "UPDATE send_log SET ref = ? WHERE item_id = ? AND version = ?", (ref, item_id, version)
        )
