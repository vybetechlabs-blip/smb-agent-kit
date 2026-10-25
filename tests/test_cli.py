import typer
from conftest import Draft, Queue
from typer.testing import CliRunner

from smb_agent_kit.cli import add_approval_commands, expand
from smb_agent_kit.delivery import make_sender


def make_app(tmp_path):
    app = typer.Typer(add_completion=False, no_args_is_help=True)

    @app.callback()
    def root() -> None:
        pass

    db = tmp_path / "q.db"
    add_approval_commands(
        app,
        open_queue=lambda: Queue(db),
        make_sender=lambda name: make_sender(name, outbox=tmp_path / "outbox"),
        reply_from=lambda: "replies@example.com",
        list_total=lambda d: "$9.00" if d["payload_json"] else "",
        show_extra=lambda d: ["", "payload: yes"] if d["payload_json"] else [],
    )
    q = Queue(db)
    q.ingest_item("r1", "{}", '{"x": 1}', Draft(payload='{"total": "9"}'), "test")
    q.close()
    return app


def test_approval_verbs_refuse_then_send_once(tmp_path):
    run = CliRunner().invoke
    app = make_app(tmp_path)
    listing = run(app, ["list"])
    assert "r1" in listing.output and "$9.00" in listing.output and "1 draft(s)" in listing.output
    refused = run(app, ["send", "r1"])
    assert refused.exit_code == 2 and "only approved or edited drafts are sent" in refused.output
    assert run(app, ["approve", "r1"]).exit_code == 0
    assert run(app, ["send", "r1"]).exit_code == 0
    again = run(app, ["send", "r1"])
    assert again.exit_code == 3 and "never sent twice" in again.output
    shown = run(app, ["show", "r1"])
    assert "payload: yes" in shown.output and "approved -> sent  by owner" in shown.output


def test_edit_needs_something_to_edit(tmp_path):
    result = CliRunner().invoke(make_app(tmp_path), ["edit", "r1"])
    assert result.exit_code == 1


def test_expand_handles_globs_itself(tmp_path):
    for name in ("b.json", "a.json"):
        (tmp_path / name).write_text("{}", encoding="utf-8")
    assert [p.name for p in expand([str(tmp_path / "*.json")])] == ["a.json", "b.json"]
