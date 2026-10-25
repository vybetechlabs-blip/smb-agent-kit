"""Saved eval runs: a base record, a JSON store, and golden-file hashing."""

from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, ClassVar

import yaml


def sha(path: Path) -> str:
    """Content hash of a golden file, with line endings normalised so CRLF and LF copies agree."""
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()[:12]


def golden_at(golden_path: Path, golden_sha: str, root: Path) -> dict:
    """The golden file as it was when a run was scored, found by hash in the working tree or git."""
    if sha(golden_path) == golden_sha:
        return yaml.safe_load(golden_path.read_text(encoding="utf-8"))
    rel = golden_path.relative_to(root).as_posix()
    revs = subprocess.run(
        ["git", "-C", str(root), "log", "--format=%H", "--", rel],
        capture_output=True, text=True, check=True,
    ).stdout.split()  # fmt: skip
    for rev in revs:
        blob = subprocess.run(
            ["git", "-C", str(root), "show", f"{rev}:{rel}"], capture_output=True, check=True
        ).stdout
        lf = blob.replace(b"\r\n", b"\n")
        # git stores LF; on Windows the working copy a run hashed may have had CRLF endings.
        for variant in (blob, lf, lf.replace(b"\n", b"\r\n")):
            if hashlib.sha256(variant).hexdigest()[:12] == golden_sha:
                return yaml.safe_load(variant.decode("utf-8"))
    raise LookupError(f"no version of {rel} with hash {golden_sha} in the tree or git history")


@dataclass
class EvalRun:
    """One saved pass of one extractor row over one golden file.

    Projects subclass it, set `case_type` to their own case dataclass, and add the measures they
    report. Field order here is the JSON field order of every saved run.
    """

    case_type: ClassVar[type] = dict

    extractor: str
    run_date: str
    cases: list[Any]
    golden_file: str = "eval/golden.yaml"
    golden_sha: str = ""
    prompt_sha: str | None = None
    status: str = "ok"  # ok | skipped
    skip_reason: str | None = None
    pass_index: int = 1
    settings: dict = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2) + "\n"

    @classmethod
    def from_json(cls, text: str):
        data = json.loads(text)
        data["cases"] = [cls.case_type(**c) for c in data["cases"]]
        return cls(**data)


def run_path(row: str, golden_path: Path, runs_dir: Path, pass_index: int = 1) -> Path:
    slug = row.replace(":", "_").replace("/", "_").replace("@", "_").replace("+", "_")
    tag = f"__p{pass_index}" if pass_index > 1 or "@" in row else ""
    return runs_dir / f"{slug}{tag}__{golden_path.stem}.json"


def save_run(run: EvalRun, golden_path: Path, runs_dir: Path) -> Path:
    path = run_path(run.extractor, golden_path, runs_dir, run.pass_index)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(run.to_json(), encoding="utf-8", newline="\n")
    return path


def load_runs(run_type: type[EvalRun], golden_path: Path, runs_dir: Path) -> list:
    if not runs_dir.exists():
        return []
    return [
        run_type.from_json(p.read_text(encoding="utf-8"))
        for p in sorted(runs_dir.glob(f"*__{golden_path.stem}.json"))
    ]
