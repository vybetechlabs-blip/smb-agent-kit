"""Evals: saved runs and report helpers.

A project scores its own cases against its own golden files. The kit stores each run as JSON
(one file per row, golden file and pass), records the hash of the golden file and prompt it ran
against, recovers an older golden file from git by that hash, and renders the parts of a Markdown
report that do not depend on what a project measures.
"""

from smb_agent_kit.evals.report import (
    cell,
    group_runs,
    label,
    pct,
    render_cost,
    render_spread,
)
from smb_agent_kit.evals.runs import (
    EvalRun,
    golden_at,
    load_runs,
    run_path,
    save_run,
    sha,
)

__all__ = [
    "EvalRun",
    "cell",
    "golden_at",
    "group_runs",
    "label",
    "load_runs",
    "pct",
    "render_cost",
    "render_spread",
    "run_path",
    "save_run",
    "sha",
]
