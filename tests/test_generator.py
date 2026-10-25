import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from new_project import generate

RULE_ONE = re.compile(r"recruit|hiring|job hunt|job search|screening|portfolio|resume|employer")


def test_generator_writes_a_project_with_visible_placeholders(tmp_path):
    target = generate("demo-agent", tmp_path, kit_path=Path.cwd())
    files = {p.relative_to(target).as_posix() for p in target.rglob("*") if p.is_file()}
    for expected in (
        "pyproject.toml",
        "README.md",
        "LICENSE",
        ".gitignore",
        ".gitattributes",
        ".github/workflows/ci.yml",
        "demo_agent/__init__.py",
        "demo_agent/cli.py",
        "tests/test_smoke.py",
        "business/business.yaml",
        "eval/golden.yaml",
        "inbox/samples/.gitkeep",
    ):
        assert expected in files, expected
    readme = (target / "README.md").read_text(encoding="utf-8")
    assert readme.count("<<FILL:") >= 5
    assert "## How AI was used" in readme and "Do not draft it" in readme
    assert "{{" not in readme and "{{" not in (target / "demo_agent" / "cli.py").read_text("utf-8")
    assert "${{ matrix.os }}" in (target / ".github/workflows/ci.yml").read_text("utf-8")


def test_generated_text_has_no_job_hunt_framing_and_no_harness_files(tmp_path):
    target = generate("demo-agent", tmp_path, kit_path=Path.cwd())
    for path in target.rglob("*"):
        if path.is_file():
            assert not RULE_ONE.search(path.read_text(encoding="utf-8").lower()), path
    names = {p.name for p in target.rglob("*")}
    assert not names & {"briefs", "AGENTS.md", "GEMINI.md", "CLAUDE.md", ".claude"}


def test_generator_pins_the_git_tag_by_default(tmp_path):
    target = generate("demo-agent", tmp_path)
    pyproject = (target / "pyproject.toml").read_text(encoding="utf-8")
    kit_git = "https://github.com/vybetechlabs-blip/smb-agent-kit"
    assert f'smb-agent-kit = {{ git = "{kit_git}", tag = "v0.1.0" }}' in pyproject


def test_generator_refuses_bad_names_and_non_empty_targets(tmp_path):
    with pytest.raises(ValueError):
        generate("Bad Name", tmp_path)
    generate("demo-agent", tmp_path, kit_path=Path.cwd())
    with pytest.raises(FileExistsError):
        generate("demo-agent", tmp_path, kit_path=Path.cwd())
