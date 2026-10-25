from pathlib import Path

import pytest
from conftest import ingest

from smb_agent_kit.approval import (
    AlreadyIngested,
    AlreadySent,
    InvalidTransition,
    NotFound,
    NotSendable,
)
from smb_agent_kit.delivery import DryRunSender

FROM = "replies@example.com"


def test_ingest_lands_in_drafted(queue):
    row = queue.get(ingest(queue))
    assert (row["state"], row["kind"], row["version"]) == ("drafted", "reply", 1)


def test_ingest_twice_is_refused(queue):
    ingest(queue)
    with pytest.raises(AlreadyIngested):
        ingest(queue)


def test_send_of_a_draft_that_is_not_approved_is_refused(queue, sender):
    item = ingest(queue)
    with pytest.raises(NotSendable) as exc:
        queue.send(item, sender, FROM)
    assert exc.value.exit_code == 2
    assert "only approved or edited drafts are sent" in str(exc.value)
    assert not sender.outbox.exists()


def test_repeat_send_of_the_same_key_is_refused_with_the_idempotency_message(queue, sender):
    item = ingest(queue)
    queue.approve(item)
    ref = queue.send(item, sender, FROM)
    assert ref.endswith("r1-v1.eml")
    with pytest.raises(AlreadySent) as exc:
        queue.send(item, sender, FROM)
    assert exc.value.exit_code == 3
    assert "already sent" in str(exc.value) and "never sent twice" in str(exc.value)
    assert len(list(sender.outbox.glob("*.eml"))) == 1


def test_an_edit_bumps_the_version_and_counts_as_approval(queue, sender):
    item = ingest(queue)
    assert queue.edit(item, body="Edited.") == 2
    row = queue.get(item)
    assert (row["state"], row["version"], row["body"]) == ("edited", 2, "Edited.")
    ref = queue.send(item, sender, FROM)
    assert ref.endswith("-v2.eml") and "Edited." in Path(ref).read_text(encoding="utf-8")


def test_every_transition_is_logged_with_an_actor(queue, sender):
    item = ingest(queue)
    queue.edit(item, subject="New", actor="alex")
    queue.approve(item, actor="alex")
    queue.send(item, sender, FROM, actor="alex")
    history = queue.show(item)["transitions"]
    assert [(t["from_state"], t["to_state"], t["actor"]) for t in history] == [
        (None, "drafted", "system"),
        ("drafted", "edited", "alex"),
        ("edited", "approved", "alex"),
        ("approved", "sent", "alex"),
    ]
    assert all(t["at"] for t in history)


def test_a_kind_marked_not_sendable_is_never_sent(queue, sender):
    item = ingest(queue, kind="note")
    queue.approve(item)
    with pytest.raises(NotSendable) as exc:
        queue.send(item, sender, FROM)
    assert str(exc.value) == "refused: r1 is an internal note for a person, not an email"


def test_rejected_and_sent_are_terminal(queue, sender):
    a = ingest(queue, "a")
    queue.reject(a)
    with pytest.raises(InvalidTransition):
        queue.approve(a)
    b = ingest(queue, "b")
    queue.approve(b)
    queue.send(b, sender, FROM)
    with pytest.raises(InvalidTransition):
        queue.edit(b, body="late")


def test_a_failed_send_leaves_no_log_and_can_be_retried(queue, tmp_path):
    class Broken:
        name = "broken"

        def send(self, **kwargs):
            raise OSError("network down")

    item = ingest(queue)
    queue.approve(item)
    with pytest.raises(OSError):
        queue.send(item, Broken(), FROM)
    assert queue.show(item)["send_log"] == []
    queue.send(item, DryRunSender(tmp_path / "out"), FROM)
    assert queue.get(item)["state"] == "sent"


def test_unknown_item_names_the_project_noun(queue):
    with pytest.raises(NotFound) as exc:
        queue.approve("nope")
    assert str(exc.value) == "no draft for request 'nope'"


def test_no_recipient_is_refused(queue, sender):
    item = ingest(queue, to=None)
    queue.approve(item)
    with pytest.raises(NotSendable):
        queue.send(item, sender, FROM)
