"""Generate a new approval-gated agent project from templates/new-project.

    uv run python scripts/new_project.py <name> --dest <folder>
    uv run python scripts/new_project.py <name> --dest <folder> --kit-path <local kit checkout>

The project depends on smb-agent-kit by pinned git tag (default `v0.1.0`). `--kit-path` points it
at a local checkout instead, which the kit's own CI uses for its generator smoke test. Every
placeholder the author must fill is marked `<<FILL: ...>>`, so a human and the voice lint both
catch an unfilled one. Public-repo rules apply from the first commit, so the output contains no
`briefs/` folder and no agent pointer files.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

KIT_ROOT = Path(__file__).resolve().parent.parent
TEMPLATES = KIT_ROOT / "templates" / "new-project"
KIT_GIT = "https://github.com/vybetechlabs-blip/smb-agent-kit"
NAME_RE = re.compile(r"^[a-z][a-z0-9-]{1,40}$")

# template path (relative to TEMPLATES) -> output path; "package" becomes the package folder.
RENAMES = {
    "gitignore.tmpl": ".gitignore",
    "gitattributes.tmpl": ".gitattributes",
    "ci.yml.tmpl": ".github/workflows/ci.yml",
}


def kit_source(tag: str, kit_path: Path | None) -> str:
    if kit_path is not None:
        return f'{{ path = "{kit_path.resolve().as_posix()}", editable = true }}'
    return f'{{ git = "{KIT_GIT}", tag = "{tag}" }}'


def generate(name: str, dest: Path, tag: str = "v0.1.0", kit_path: Path | None = None) -> Path:
    if not NAME_RE.match(name):
        raise ValueError(f"{name!r}: use lowercase letters, digits and hyphens")
    target = dest / name
    if target.exists() and any(target.iterdir()):
        raise FileExistsError(f"{target} exists and is not empty")
    package = name.replace("-", "_")
    tokens = {
        "{{name}}": name,
        "{{package}}": package,
        "{{env_prefix}}": package.upper(),
        "{{kit_source}}": kit_source(tag, kit_path),
    }
    for src in sorted(TEMPLATES.rglob("*.tmpl")):
        rel = src.relative_to(TEMPLATES).as_posix()
        out_rel = RENAMES.get(rel, rel.removesuffix(".tmpl"))
        out_rel = re.sub(r"^package/", f"{package}/", out_rel)
        text = src.read_text(encoding="utf-8")
        if not out_rel.endswith("ci.yml"):  # GitHub expressions use ${{ }}; leave them alone
            for token, value in tokens.items():
                text = text.replace(token, value)
        out = target / out_rel
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8", newline="\n")
    (target / "inbox" / "samples").mkdir(parents=True, exist_ok=True)
    (target / "inbox" / "samples" / ".gitkeep").write_text("", encoding="utf-8")
    return target


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("name")
    parser.add_argument("--dest", type=Path, required=True)
    parser.add_argument("--tag", default="v0.1.0", help="kit git tag to pin")
    parser.add_argument("--kit-path", type=Path, help="local kit checkout instead of the git tag")
    args = parser.parse_args(argv)
    target = generate(args.name, args.dest, args.tag, args.kit_path)
    print(f"generated {target}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
