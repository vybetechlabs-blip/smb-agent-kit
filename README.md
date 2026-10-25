# smb-agent-kit

[![CI](https://github.com/vybetechlabs-blip/smb-agent-kit/actions/workflows/ci.yml/badge.svg)](https://github.com/vybetechlabs-blip/smb-agent-kit/actions/workflows/ci.yml)

**Status: Active, v0.1.0 in progress, public release pending.**

## Problem and result
Small-business agents tend to share one shape: read something in, extract or classify it, decide, draft a reply, wait for a person to approve it, send it, log every step, and measure the whole thing against a golden set. Building the approval queue, the send log, the model providers and the eval runner again for every agent is where most of the time goes. This kit is a proof of concept that lifts those parts out of one working agent, [`enquiry-to-quote`](https://github.com/vybetechlabs-blip/enquiry-to-quote), so the next agent starts at "write the extractor and the decision rule".

The kit has 36 tests, and they run without any API key or network. A generator turns out a new project that installs, passes its stub tests and lints clean. CI runs that generator smoke test on every push.

`enquiry-to-quote` runs on this kit with no change in behavior. On its `refactor/use-kit` branch, its 119 tests pass unchanged. Its eval report and all 42 saved run files come out byte-identical to `main` after re-scoring from saved outputs, with no model calls. Both of its CLI refusals (an unapproved send, then a repeat send) reproduce with the same messages and exit codes.

### How it works
```
smb_agent_kit/
  approval/   SQLite queue: drafted → approved | edited | rejected → sent, transition log with actor
  delivery/   Sender protocol, DryRunSender (outbox folder), ResendSender (key), SendLog keyed by (item, version)
  llm/        ScriptedLLM, OllamaLLM (127.0.0.1, warm-up, one retry), AnthropicLLM, CachedLLM,
              structured_output(): parsed data or a failure reason, plus a per-call record
  evals/      saved runs (JSON per row, golden file and pass), golden-file hash and lookup in git,
              report helpers: multi-pass cells, outcome spread, cost and latency table
  config/     YAML loading; a business config must declare `fictional: true`
  cli.py      list / show / approve / edit / reject / send, mounted on a project's own CLI
templates/new-project/ + scripts/new_project.py   a new project, pinned to a kit tag
```

A project keeps its own extractor, decision rule, drafts, golden sets and measures. The kit knows nothing about quotes, invoices or appointments: projects depend on the kit, never the reverse.

### The approval boundary
- `send` refuses any draft that is not `approved` or `edited`. Draft kinds a project marks as not sendable (an internal handoff note, say) are refused outright.
- The send log is keyed by item id and draft version, and the log row is written in the same transaction as the send. A second send of the same key is refused with an error (exit code 3); it is never skipped silently.
- Every transition is logged with a timestamp and an actor.

## Decision log
**1. Extract from one working agent, not design in the abstract.**
Chosen: every module here already existed and was measured in `enquiry-to-quote` before it moved. A unit with only one user stays in that project; for example, `enquiry-to-quote`'s extractor cross-check and its cover-note number check.
Rejected: a framework designed ahead of its users, with plugin registries or a scheduler.

**2. Projects depend on a pinned git tag.**
Chosen: `smb-agent-kit = { git = "https://github.com/vybetechlabs-blip/smb-agent-kit", tag = "v0.1.0" }` in the project's `pyproject.toml`, which needs the kit to be public.
Rejected: vendoring a copy into each project, which lets copies drift apart.

**3. Keyless by default.**
Chosen: the kit's tests use `ScriptedLLM` only, and Ollama and Anthropic are opt-in. A project's tests and demo run with no account.
Rejected: tests that need a key, which nobody else can run.

**4. Real business data never ships.**
Chosen: the config loader refuses a business config that does not carry `fictional: true`.
Rejected: a convention without a check.

**5. Unfilled placeholders stay visible.**
Chosen: the generator marks every part a person must write as `<<FILL: …>>`, including the "How AI was used" section, which the generator never drafts.
Rejected: plausible default text that passes review unread.

## How AI was used
This kit is the part of enquiry-to-quote that every approval-gated agent needs, pulled out after that project worked rather than designed in the abstract. I wrote the brief with Claude and set the rules: nothing is abstracted until a real project has used it, the kit knows nothing about quotes or any other domain, and the first project moved onto it had to produce a byte-identical eval report before the release was tagged. Claude Code did the read-only inventory first, then the extraction, the generator and the tests, and it was Claude Code that flagged two things my brief had wrong: it pointed at a commit that predated most of the code worth extracting, and it assumed a public project could depend on a private kit. Making the kit public from its first release, pinning it by git tag, keeping the fictional-data check as a required field and the pass condition were my calls. The refactor was verified by re-scoring 22 saved evaluation runs with no model calls; the report and all 42 run files came out unchanged.

## Run it locally
You need Python 3.11 or newer and [uv](https://docs.astral.sh/uv/). These commands are for PowerShell; they also work in bash.

```powershell
uv sync
uv run pytest                                            # 36 tests, no key or network
uv run ruff check . ; uv run ruff format --check .
uv run python scripts/new_project.py my-agent --dest ..  # a new project, pinned to the kit tag
```

`--kit-path <kit checkout>` points a generated project at a local kit instead of the tag, which is how CI tests the generator.

License: MIT, see [LICENSE](LICENSE). Projects made by the generator carry the same license.

## Status
**Status: Active, v0.1.0 in progress, public release pending.** The test and lint commands are listed under Run it locally.
