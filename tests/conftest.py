from dataclasses import dataclass, field
from typing import ClassVar

import pytest

from smb_agent_kit.approval import ApprovalQueue
from smb_agent_kit.delivery import DryRunSender


@dataclass
class Draft:
    kind: str = "reply"
    to: str | None = "someone@example.com"
    subject: str = "Your reply"
    body: str = "Hello."
    reasons: list[str] = field(default_factory=list)
    payload: str | None = None

    @property
    def payload_json(self) -> str | None:
        return self.payload


class Queue(ApprovalQueue):
    noun: ClassVar[str] = "request"
    not_sendable: ClassVar[dict[str, str]] = {"note": "an internal note for a person, not an email"}


@pytest.fixture
def queue(tmp_path):
    q = Queue(tmp_path / "queue.db")
    yield q
    q.close()


@pytest.fixture
def sender(tmp_path):
    return DryRunSender(tmp_path / "outbox")


def ingest(queue, item_id="r1", **draft):
    queue.ingest_item(item_id, '{"raw": 1}', '{"field": "value"}', Draft(**draft), "test")
    return item_id
