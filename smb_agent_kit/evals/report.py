"""Report helpers that do not depend on what a project measures.

A project renders its own measure tables and wording; these helpers supply the shared parts:
percent and multi-pass cells, grouping runs by row, row labels (passes, skipped, stale), the
outcome-spread table, and the cost and latency table priced from a project's price file.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from pathlib import Path

from smb_agent_kit.evals.runs import EvalRun, sha

Display = Callable[[str], str]


def pct(ok: int, n: int) -> str:
    return f"{ok}/{n} ({100 * ok / n:.0f}%)" if n else "n/a"


def cell(values: list[tuple[int, int]]) -> str:
    """One pass: `ok/n (pct%)`. Several passes: the mean, then the lowest and highest pass."""
    if len(values) == 1:
        return pct(*values[0])
    oks = [ok for ok, _ in values]
    n = values[0][1]
    mean = sum(oks) / len(oks)
    return f"{mean:.1f}/{n} ({min(oks)}-{max(oks)})" if n else "n/a"


def group_runs(
    results: Iterable[EvalRun], display: Display = str, first: str | None = None
) -> dict[str, list]:
    """Runs grouped by row name, passes in order; `first` (for example the baseline) leads."""
    groups: dict[str, list] = {}
    for r in results:
        groups.setdefault(r.extractor, []).append(r)
    order = sorted(groups, key=lambda n: (n != first, display(n)))
    return {n: sorted(groups[n], key=lambda r: r.pass_index) for n in order}


def label(name: str, runs: list, current: str, display: Display = str) -> str:
    ok = [r for r in runs if r.status == "ok"]
    if not ok:
        return f"{display(name)} (skipped)"
    text = display(name) + (f" x{len(ok)}" if len(ok) > 1 else "")
    if all(r.golden_sha != current for r in ok):
        text += " (stale)"
    return text


def render_spread(
    groups: dict[str, list],
    current: str,
    outcome: Callable[[EvalRun], tuple[int, int]],
    display: Display = str,
) -> list[str]:
    """Best, worst and mean outcome counts for every row with more than one current pass."""
    multi = {k: [r for r in v if r.status == "ok"] for k, v in groups.items()}
    multi = {k: v for k, v in multi.items() if len(v) > 1 and v[0].golden_sha == current}
    if not multi:
        return []
    out = [
        "### Outcome spread across passes",
        "",
        "| Row | Passes | Best | Worst | Mean |",
        "|---|---|---|---|---|",
    ]
    for name, runs in multi.items():
        oks = [outcome(r)[0] for r in runs]
        total = outcome(runs[0])[1]
        out.append(
            f"| {display(name)} | {len(runs)} | {max(oks)}/{total} | {min(oks)}/{total} "
            f"| {sum(oks) / len(oks):.1f}/{total} |"
        )
    return [*out, ""]


def render_cost(
    sets: list[tuple[str, Path, list]],
    prices: dict,
    *,
    intro: list[str],
    noun: str = "item",
    display: Display = str,
    include: Callable[[EvalRun], bool] = lambda r: True,
    counts_retries: Callable[[str], bool] = lambda name: True,
) -> list[str]:
    """Calls, tokens, latency, retries and cost per row, over current runs only.

    Cases must carry `tokens_in`, `tokens_out`, `latency_s` and `retries`. `prices` maps a model
    id to either `{local: true, note}` or `{input_per_mtok, output_per_mtok, condition, source,
    fetched}`; a model with no entry is priced as `unverified`.
    """
    groups: dict[str, list] = {}
    passes: dict[str, set[int]] = {}
    settings: dict[str, dict] = {}
    for _title, golden_path, results in sets:
        current = sha(golden_path)
        for r in results:
            if r.status != "ok" or not r.prompt_sha or not include(r):
                continue
            if r.golden_sha != current:
                continue
            groups.setdefault(r.extractor, []).extend(c for c in r.cases if c.tokens_in is not None)
            passes.setdefault(r.extractor, set()).add(r.pass_index)
            settings[r.extractor] = r.settings
    if not groups:
        return []
    out = [
        "## Cost and latency (LLM rows)",
        "",
        *intro,
        "",
        f"| Row | Passes | Calls | Retries | Mean tokens in | Mean tokens out | Mean latency (s) | Max latency (s) | Cost per {noun} | Cost, all calls |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    notes = []
    for name in sorted(groups, key=display):
        cases = groups[name]
        model = name.split(":", 1)[1].split("@", 1)[0] if ":" in name else name
        price = prices.get(model, {})
        k = len(cases)
        if not k:
            continue
        tin = sum(c.tokens_in for c in cases) / k
        tout = sum(c.tokens_out for c in cases) / k
        lat = [c.latency_s for c in cases]
        if price.get("local"):
            per, total = "$0 (local)", "$0"
        elif "input_per_mtok" in price:
            pin, pout = float(price["input_per_mtok"]) / 1e6, float(price["output_per_mtok"]) / 1e6
            per = f"${tin * pin + tout * pout:.6f}"
            total = f"${sum(c.tokens_in * pin + c.tokens_out * pout for c in cases):.4f}"
        else:
            per, total = "unverified", "unverified"
        retries = str(sum(c.retries or 0 for c in cases)) if counts_retries(name) else "n/a (SDK)"
        out.append(
            f"| {display(name)} | {len(passes[name])} | {k} | {retries} | {tin:,.0f} | {tout:,.0f} | {sum(lat) / k:.1f} "
            f"| {max(lat):.1f} | {per} | {total} |"
        )
        if settings.get(name):
            notes.append(
                f"- `{display(name)}` settings: "
                + ", ".join(f"{a} {b}" for a, b in settings[name].items())
                + "."
            )
    for model, price in prices.items():
        if price.get("source"):
            notes.append(
                f"- `{model}` price: ${price['input_per_mtok']} / MTok in, ${price['output_per_mtok']} / MTok out "
                f"({price.get('condition', '')}); source {price['source']}, fetched {price['fetched']}."
            )
        elif price.get("local"):
            notes.append(f"- `{model}`: {price.get('note', 'runs locally')}")
    return [*out, "", *notes, ""]
