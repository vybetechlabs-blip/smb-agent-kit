"""The approval verbs as a command group a project mounts on its own Typer app.

`add_approval_commands` registers `list`, `show`, `approve`, `edit`, `reject` and `send`. The
project supplies how to open its queue, how to build a sender, its reply address, and two
display hooks for its own payload: a TOTAL column in `list` and extra lines in `show`.
"""

from __future__ import annotations

import glob
import json
from collections.abc import Callable
from pathlib import Path

import typer

from smb_agent_kit.approval import ApprovalQueue, QueueError
from smb_agent_kit.delivery import Sender, SenderUnavailable


def expand(patterns: list[str]) -> list[Path]:
    """Expand globs here: PowerShell passes `*.json` through unexpanded."""
    paths: list[Path] = []
    for pattern in patterns:
        matches = sorted(glob.glob(pattern)) if any(c in pattern for c in "*?[") else [pattern]
        paths += [Path(m) for m in matches]
    return paths


def split_row(row: str) -> tuple[str, str]:
    """`ollama:gpt-oss:20b` -> ("ollama", "gpt-oss:20b"). The model id is never guessed."""
    provider, _, model = row.partition(":")
    if not model:
        raise typer.BadParameter(f"{row!r}: use provider:model, e.g. ollama:gpt-oss:20b")
    return provider, model


def fail(err: QueueError) -> None:
    typer.echo(str(err), err=True)
    raise typer.Exit(err.exit_code)


def add_approval_commands(
    app: typer.Typer,
    *,
    open_queue: Callable[[], ApprovalQueue],
    make_sender: Callable[[str], Sender],
    reply_from: Callable[[], str],
    actor: str = "owner",
    list_total: Callable[[dict], str] = lambda draft: "",
    show_extra: Callable[[dict], list[str]] = lambda draft: [],
) -> None:
    @app.command("list")
    def list_cmd() -> None:
        """List every draft with its kind, state and total."""
        queue = open_queue()
        rows = queue.list()
        typer.echo(f"{'ID':<28} {'KIND':<14} {'STATE':<9} {'VER':>3}  {'TOTAL':>12}  TO")
        for r in rows:
            total = list_total(dict(r))
            typer.echo(
                f"{r['item_id']:<28} {r['kind']:<14} {r['state']:<9} {r['version']:>3}  "
                f"{total:>12}  {r['to_addr'] or '-'}"
            )
        typer.echo(f"{len(rows)} draft(s)")
        queue.close()

    @app.command()
    def show(item_id: str) -> None:
        """Show one draft, what was extracted, and its full transition and send history."""
        queue = open_queue()
        try:
            info = queue.show(item_id)
        except QueueError as err:
            fail(err)
        d = info["draft"]
        typer.echo(f"{d['item_id']}  kind={d['kind']}  state={d['state']}  version={d['version']}")
        typer.echo(f"To: {d['to_addr'] or '-'}\nSubject: {d['subject']}\n")
        typer.echo(d["body"])
        for line in show_extra(d):
            typer.echo(line)
        typer.echo("\nextracted:")
        typer.echo(json.dumps(info["item"], indent=2))
        typer.echo("\nhistory:")
        for t in info["transitions"]:
            note = f"  ({t['note']})" if t["note"] else ""
            typer.echo(
                f"  {t['at']}  v{t['version']}  {t['from_state'] or '-'} -> {t['to_state']}"
                f"  by {t['actor']}{note}"
            )
        for s in info["send_log"]:
            typer.echo(f"  sent v{s['version']} at {s['sent_at']} via {s['sender']}: {s['ref']}")
        queue.close()

    @app.command()
    def approve(item_id: str) -> None:
        """Approve a draft for sending."""
        queue = open_queue()
        try:
            queue.approve(item_id, actor=actor)
        except QueueError as err:
            fail(err)
        typer.echo(f"approved  {item_id}")
        queue.close()

    @app.command()
    def reject(item_id: str, note: str = typer.Option(None, help="Why it was rejected.")) -> None:
        """Reject a draft. Rejected drafts can never be sent."""
        queue = open_queue()
        try:
            queue.reject(item_id, actor=actor, note=note)
        except QueueError as err:
            fail(err)
        typer.echo(f"rejected  {item_id}")
        queue.close()

    @app.command()
    def edit(
        item_id: str,
        body_file: Path = typer.Option(None, help="File holding the new body."),
        subject: str = typer.Option(None, help="New subject line."),
    ) -> None:
        """Replace the subject and/or body. Saved as a new version in state `edited`."""
        if body_file is None and subject is None:
            typer.echo("nothing to edit: pass --body-file and/or --subject", err=True)
            raise typer.Exit(1)
        body = body_file.read_text(encoding="utf-8") if body_file else None
        queue = open_queue()
        try:
            version = queue.edit(item_id, body=body, subject=subject, actor=actor)
        except QueueError as err:
            fail(err)
        typer.echo(f"edited    {item_id} -> v{version}")
        queue.close()

    @app.command()
    def send(
        item_id: str,
        sender: str = typer.Option(
            "dry-run", help="dry-run (writes outbox/) or resend (needs key)."
        ),
    ) -> None:
        """Send an approved or edited draft. Refuses anything else, and refuses a repeat send."""
        try:
            backend = make_sender(sender)
        except SenderUnavailable as exc:
            typer.echo(f"refused: {exc}", err=True)
            raise typer.Exit(2) from exc
        queue = open_queue()
        try:
            ref = queue.send(item_id, backend, reply_from(), actor=actor)
        except QueueError as err:
            fail(err)
        typer.echo(f"sent      {item_id} via {backend.name}: {ref}")
        queue.close()
