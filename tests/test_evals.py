from dataclasses import dataclass

from smb_agent_kit.evals import (
    EvalRun,
    cell,
    group_runs,
    label,
    load_runs,
    pct,
    render_cost,
    render_spread,
    save_run,
    sha,
)


@dataclass
class Case:
    sample_id: str
    ok: bool
    tokens_in: int | None = None
    tokens_out: int | None = None
    latency_s: float | None = None
    retries: int | None = None


class Run(EvalRun):
    case_type = Case

    def outcome(self) -> tuple[int, int]:
        return sum(c.ok for c in self.cases), len(self.cases)


def make_runs(golden_sha: str) -> list[Run]:
    return [
        Run("baseline", "2026-10-25", [Case("a", True), Case("b", False)], golden_sha=golden_sha),
        *(
            Run(
                "local:model@v2",
                "2026-10-25",
                [Case("a", True, 100, 50, 1.5, 0), Case("b", p == 1, 120, 60, 2.5, 1)],
                golden_sha=golden_sha,
                prompt_sha="abc123",
                pass_index=p,
            )
            for p in (1, 2)
        ),
    ]


def test_sha_ignores_line_endings(tmp_path):
    lf, crlf = tmp_path / "lf.yaml", tmp_path / "crlf.yaml"
    lf.write_bytes(b"a: 1\nb: 2\n")
    crlf.write_bytes(b"a: 1\r\nb: 2\r\n")
    assert sha(lf) == sha(crlf)


def test_runs_round_trip_byte_for_byte(tmp_path):
    golden = tmp_path / "golden.yaml"
    golden.write_text("a: {}\n", encoding="utf-8")
    for run in make_runs(sha(golden)):
        save_run(run, golden, tmp_path / "runs")
    first = {p.name: p.read_bytes() for p in (tmp_path / "runs").glob("*.json")}
    for run in load_runs(Run, golden, tmp_path / "runs"):
        save_run(run, golden, tmp_path / "runs")
    second = {p.name: p.read_bytes() for p in (tmp_path / "runs").glob("*.json")}
    assert first == second and len(first) == 3


def test_report_helpers_are_stable_same_input_same_bytes(tmp_path):
    golden = tmp_path / "golden.yaml"
    golden.write_text("a: {}\n", encoding="utf-8")
    current = sha(golden)

    def render() -> str:
        runs = make_runs(current)
        groups = group_runs(runs, first="baseline")
        lines = [label(k, v, current) for k, v in groups.items()]
        lines += render_spread(groups, current, Run.outcome)
        prices = {"model": {"local": True, "note": "runs on this machine"}}
        lines += render_cost(
            [("Set", golden, runs)], prices, intro=["One call per item."], noun="item"
        )
        return "\n".join(lines)

    first, second = render(), render()
    assert first == second
    assert "| local:model@v2 | 2 | 4 | 2 | 110 | 55 | 2.0 | 2.5 | $0 (local) | $0 |" in first
    assert "| local:model@v2 | 2 | 2/2 | 1/2 | 1.5/2 |" in first
    assert first.splitlines()[0] == "baseline"


def test_cells_and_labels():
    assert pct(3, 4) == "3/4 (75%)" and pct(0, 0) == "n/a"
    assert cell([(3, 4)]) == "3/4 (75%)"
    assert cell([(3, 4), (4, 4), (2, 4)]) == "3.0/4 (2-4)"
    runs = make_runs("old")
    assert label("local:model@v2", runs[1:], "new") == "local:model@v2 x2 (stale)"
    skipped = Run("hosted:model", "2026-10-25", [], status="skipped", skip_reason="no key")
    assert label("hosted:model", [skipped], "new") == "hosted:model (skipped)"
