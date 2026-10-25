"""Approval queue: one SQLite file, a state machine, and the idempotent send log.

States: drafted -> (approved | edited | rejected) -> sent.
Nothing reaches a sender unless the draft is `approved` or `edited`. Draft kinds a project marks
as not sendable (for example an internal handoff note) are refused outright. The send log is keyed
by item id + draft version, and a second send of a key that is already logged is refused with an
error, never skipped silently. Every transition is logged with a timestamp and an actor.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import ClassVar, Protocol

from smb_agent_kit.delivery import Sender, SendLog, send_key

ALLOWED = {
    "drafted": {"approved", "edited", "rejected"},
    "approved": {"edited", "rejected", "sent"},
    "edited": {"approved", "edited", "rejected", "sent"},
    "rejected": set(),
    "sent": set(),
}
SENDABLE_STATES = {"approved", "edited"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
    item_id TEXT PRIMARY KEY,
    raw_json TEXT NOT NULL,
    extracted_json TEXT NOT NULL,
    extractor TEXT NOT NULL,
    ingested_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS drafts (
    item_id TEXT PRIMARY KEY REFERENCES items(item_id),
    version INTEGER NOT NULL,
    kind TEXT NOT NULL,
    state TEXT NOT NULL,
    to_addr TEXT,
    subject TEXT NOT NULL,
    body TEXT NOT NULL,
    payload_json TEXT,
    reasons_json TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS draft_versions (
    item_id TEXT NOT NULL,
    version INTEGER NOT NULL,
    subject TEXT NOT NULL,
    body TEXT NOT NULL,
    actor TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (item_id, version)
);
CREATE TABLE IF NOT EXISTS transitions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id TEXT NOT NULL,
    version INTEGER NOT NULL,
    from_state TEXT,
    to_state TEXT NOT NULL,
    actor TEXT NOT NULL,
    at TEXT NOT NULL,
    note TEXT
);
"""


class Draft(Protocol):
    """What the queue needs from a project's draft. `payload_json` is project data (or None)."""

    kind: str
    to: str | None
    subject: str
    body: str
    reasons: list[str]

    @property
    def payload_json(self) -> str | None: ...


class QueueError(Exception):
    exit_code = 1


class NotFound(QueueError):
    exit_code = 1


class AlreadyIngested(QueueError):
    exit_code = 1


class InvalidTransition(QueueError):
    exit_code = 2


class NotSendable(QueueError):
    exit_code = 2


class AlreadySent(QueueError):
    exit_code = 3


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class ApprovalQueue:
    """`noun` names an item in messages ("inquiry", "invoice"). `not_sendable` maps a draft kind
    that must never be sent to the phrase used in its refusal."""

    noun: ClassVar[str] = "item"
    not_sendable: ClassVar[dict[str, str]] = {}

    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.db = sqlite3.connect(self.path, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        self.send_log = SendLog(self.db)

    def close(self) -> None:
        self.db.close()

    # --- reads ----------------------------------------------------------------------------

    def get(self, item_id: str) -> sqlite3.Row:
        row = self.db.execute("SELECT * FROM drafts WHERE item_id = ?", (item_id,)).fetchone()
        if row is None:
            raise NotFound(f"no draft for {self.noun} {item_id!r}")
        return row

    def list(self) -> list[sqlite3.Row]:
        return self.db.execute(
            "SELECT d.*, i.ingested_at FROM drafts d JOIN items i USING (item_id) "
            "ORDER BY i.ingested_at, d.item_id"
        ).fetchall()

    def show(self, item_id: str) -> dict:
        draft = self.get(item_id)
        item = self.db.execute("SELECT * FROM items WHERE item_id = ?", (item_id,)).fetchone()
        transitions = self.db.execute(
            "SELECT * FROM transitions WHERE item_id = ? ORDER BY id", (item_id,)
        ).fetchall()
        return {
            "draft": dict(draft),
            "item": json.loads(item["extracted_json"]),
            "transitions": [dict(t) for t in transitions],
            "send_log": [dict(s) for s in self.send_log.history(item_id)],
        }

    # --- writes ---------------------------------------------------------------------------

    def _log(self, item_id, version, from_state, to_state, actor, note=None) -> None:
        self.db.execute(
            "INSERT INTO transitions (item_id, version, from_state, to_state, actor, at, note) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (item_id, version, from_state, to_state, actor, now(), note),
        )

    def _check(self, row: sqlite3.Row, to_state: str) -> None:
        if to_state not in ALLOWED[row["state"]]:
            raise InvalidTransition(
                f"{row['item_id']}: cannot go from {row['state']} to {to_state}"
            )

    def ingest_item(
        self, item_id: str, raw_json: str, extracted_json: str, draft: Draft, extractor: str
    ) -> None:
        exists = self.db.execute("SELECT 1 FROM items WHERE item_id = ?", (item_id,)).fetchone()
        if exists:
            raise AlreadyIngested(f"{item_id} is already in the queue")
        ts = now()
        self.db.execute("BEGIN IMMEDIATE")
        try:
            self.db.execute(
                "INSERT INTO items VALUES (?, ?, ?, ?, ?)",
                (item_id, raw_json, extracted_json, extractor, ts),
            )
            self.db.execute(
                "INSERT INTO drafts VALUES (?, 1, ?, 'drafted', ?, ?, ?, ?, ?, ?)",
                (
                    item_id,
                    draft.kind,
                    draft.to,
                    draft.subject,
                    draft.body,
                    draft.payload_json,
                    json.dumps(draft.reasons),
                    ts,
                ),
            )
            self.db.execute(
                "INSERT INTO draft_versions VALUES (?, 1, ?, ?, 'system', ?)",
                (item_id, draft.subject, draft.body, ts),
            )
            self._log(item_id, 1, None, "drafted", "system", note=draft.kind)
            self.db.execute("COMMIT")
        except Exception:
            self.db.execute("ROLLBACK")
            raise

    def _transition(self, item_id: str, to_state: str, actor: str, note: str | None) -> None:
        row = self.get(item_id)
        self._check(row, to_state)
        self.db.execute("BEGIN IMMEDIATE")
        self.db.execute(
            "UPDATE drafts SET state = ?, updated_at = ? WHERE item_id = ?",
            (to_state, now(), item_id),
        )
        self._log(item_id, row["version"], row["state"], to_state, actor, note)
        self.db.execute("COMMIT")

    def approve(self, item_id: str, actor: str = "owner") -> None:
        self._transition(item_id, "approved", actor, None)

    def reject(self, item_id: str, actor: str = "owner", note: str | None = None) -> None:
        self._transition(item_id, "rejected", actor, note)

    def edit(
        self,
        item_id: str,
        *,
        body: str | None = None,
        subject: str | None = None,
        actor: str = "owner",
    ) -> int:
        """Save an edited draft as a new version. An edit by the owner counts as approval."""
        row = self.get(item_id)
        self._check(row, "edited")
        version = row["version"] + 1
        new_subject = subject if subject is not None else row["subject"]
        new_body = body if body is not None else row["body"]
        ts = now()
        self.db.execute("BEGIN IMMEDIATE")
        self.db.execute(
            "UPDATE drafts SET version = ?, state = 'edited', subject = ?, body = ?, "
            "updated_at = ? WHERE item_id = ?",
            (version, new_subject, new_body, ts, item_id),
        )
        self.db.execute(
            "INSERT INTO draft_versions VALUES (?, ?, ?, ?, ?, ?)",
            (item_id, version, new_subject, new_body, actor, ts),
        )
        self._log(item_id, version, row["state"], "edited", actor)
        self.db.execute("COMMIT")
        return version

    def send(self, item_id: str, sender: Sender, from_addr: str, actor: str = "owner") -> str:
        row = self.get(item_id)
        version = row["version"]
        key = send_key(item_id, version)
        logged = self.send_log.lookup(item_id, version)
        if logged:
            raise AlreadySent(
                f"refused: {key} was already sent at {logged['sent_at']} via {logged['sender']} "
                f"(ref {logged['ref']}). The same key is never sent twice."
            )
        if row["kind"] in self.not_sendable:
            raise NotSendable(f"refused: {item_id} is {self.not_sendable[row['kind']]}")
        if row["state"] not in SENDABLE_STATES:
            raise NotSendable(
                f"refused: {item_id} is {row['state']}; only approved or edited drafts are sent"
            )
        if not row["to_addr"]:
            raise NotSendable(f"refused: {item_id} has no recipient address")

        self.db.execute("BEGIN IMMEDIATE")
        try:
            self.send_log.record(item_id, version, now(), sender.name)
            ref = sender.send(
                key=key,
                to=row["to_addr"],
                sender=from_addr,
                subject=row["subject"],
                body=row["body"],
            )
            self.send_log.set_ref(item_id, version, ref)
            self.db.execute(
                "UPDATE drafts SET state = 'sent', updated_at = ? WHERE item_id = ?",
                (now(), item_id),
            )
            self._log(item_id, version, row["state"], "sent", actor, note=f"{sender.name}: {ref}")
            self.db.execute("COMMIT")
        except sqlite3.IntegrityError as exc:
            self.db.execute("ROLLBACK")
            raise AlreadySent(f"refused: {key} is already in the send log") from exc
        except Exception:
            self.db.execute("ROLLBACK")
            raise
        return ref
