#!/usr/bin/env python3
"""
generate-diagnose-iwe-skill.py

SoT: shared/rubrics/form-089.yaml
SKILL.md: секции между маркерами <!-- RUBRIC-AUTO:name vX.Y --> генерируются из YAML.
Алгоритмические части (DRILL_MAP, mandatory list, save-код) остаются статическими в SKILL.md.

Usage:
  python3 scripts/generate-diagnose-iwe-skill.py           # regenerate markers in SKILL.md
  python3 scripts/generate-diagnose-iwe-skill.py --check   # verify sync, exit 1 if stale
"""
import argparse
import pathlib
import re
import sys

try:
    import yaml
except ImportError:
    print("ERROR: pyyaml not installed. Run: pip install pyyaml", file=sys.stderr)
    sys.exit(1)

REPO_ROOT = pathlib.Path(__file__).parent.parent
YAML_PATH = REPO_ROOT / "shared" / "rubrics" / "form-089.yaml"
SKILL_PATH = REPO_ROOT / ".claude" / "skills" / "diagnose-iwe" / "SKILL.md"


# ---------------------------------------------------------------------------
# Section generators — each returns a string that goes between markers
# ---------------------------------------------------------------------------

def _phase1_block(data: dict) -> str:
    slots = {k: v for k, v in data["slots"].items() if v.get("phase") == 1}
    ordered = sorted(slots.items(), key=lambda x: x[1].get("order", 99))
    lines = []
    for i, (key, slot) in enumerate(ordered, 1):
        q = slot["question"].strip().replace("\n", " ")
        info = ", информационный" if slot.get("informational") else ""
        lines.append(f"**Вопрос {i} ({key}{info}):**")
        lines.append(q)
        lines.append("")
        for val in sorted(slot["scale"].keys()):
            lines.append(f"{val} — {slot['scale'][val]}")
        lines.append("")
    while lines and lines[-1] == "":
        lines.pop()
    return "\n".join(lines)


def _drill_block(data: dict) -> str:
    dd = data.get("drill_down", {})
    lines = []
    for key, slot in dd.items():
        q = slot["question"].strip()
        lines.append(f"**{key}** — {slot['name']}")
        lines.append(q)
        for val in sorted(slot["scale"].keys()):
            lines.append(f"{val} — {slot['scale'][val]}")
        lines.append("")
    while lines and lines[-1] == "":
        lines.pop()
    return "\n".join(lines)


def _formulas_block(data: dict) -> str:
    scoring = data.get("scoring", {})
    stage = scoring.get("stage_formula", "")
    bn = scoring.get("bottleneck_formula", "")
    return f"stage:      `{stage}`\nbottleneck: `{bn}`"


SECTION_GENERATORS = {
    "phase1": _phase1_block,
    "drill_down": _drill_block,
    "formulas": _formulas_block,
}


# ---------------------------------------------------------------------------
# Marker helpers
# ---------------------------------------------------------------------------

def _marker_start(name: str, version: str) -> str:
    return f"<!-- RUBRIC-AUTO:{name} v{version} -->"


def _marker_end(name: str) -> str:
    return f"<!-- /RUBRIC-AUTO:{name} -->"


def _section_pattern(name: str) -> re.Pattern:
    return re.compile(
        rf"<!-- RUBRIC-AUTO:{re.escape(name)} v[0-9.]+ -->\n(.*?)\n<!-- /RUBRIC-AUTO:{re.escape(name)} -->",
        re.DOTALL,
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def generate() -> None:
    """Regenerate RUBRIC-AUTO marker sections in SKILL.md from YAML SoT."""
    data = yaml.safe_load(YAML_PATH.read_text(encoding="utf-8"))
    version = data.get("schema_version", "?")
    skill_text = SKILL_PATH.read_text(encoding="utf-8")

    changed = False
    for name, fn in SECTION_GENERATORS.items():
        content = fn(data)
        start = _marker_start(name, version)
        end = _marker_end(name)
        replacement = f"{start}\n{content}\n{end}"
        new_text, count = _section_pattern(name).subn(replacement, skill_text)
        if count == 0:
            print(f"WARNING: marker '{name}' not found in SKILL.md — skipped", file=sys.stderr)
        elif new_text != skill_text:
            changed = True
        skill_text = new_text

    SKILL_PATH.write_text(skill_text, encoding="utf-8")
    status = "updated" if changed else "already in sync"
    print(f"OK: SKILL.md {status} (YAML v{version}).")


def check() -> None:
    """Verify RUBRIC-AUTO marker sections match current YAML. Exit 1 if stale."""
    if not YAML_PATH.exists():
        print(f"ERROR: YAML not found: {YAML_PATH}", file=sys.stderr)
        sys.exit(1)
    if not SKILL_PATH.exists():
        print(f"ERROR: SKILL.md not found: {SKILL_PATH}", file=sys.stderr)
        sys.exit(1)

    try:
        data = yaml.safe_load(YAML_PATH.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        print(f"ERROR: Invalid YAML: {e}", file=sys.stderr)
        sys.exit(1)

    version = data.get("schema_version", "?")
    skill_text = SKILL_PATH.read_text(encoding="utf-8")

    missing = []
    for name, fn in SECTION_GENERATORS.items():
        content = fn(data)
        start = _marker_start(name, version)
        end = _marker_end(name)
        expected_block = f"{start}\n{content}\n{end}"
        if expected_block not in skill_text:
            missing.append(f"Section '{name}' v{version} missing or stale")

    if missing:
        print("ERROR: diagnose-iwe/SKILL.md is stale.", file=sys.stderr)
        for m in missing:
            print(f"  - {m}", file=sys.stderr)
        print("  Fix: python3 scripts/generate-diagnose-iwe-skill.py", file=sys.stderr)
        print("  Then: git add shared/rubrics/form-089.yaml .claude/skills/diagnose-iwe/SKILL.md", file=sys.stderr)
        sys.exit(1)

    print(f"OK: diagnose-iwe/SKILL.md is in sync with YAML v{version}.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="Verify sync without writing")
    args = parser.parse_args()

    if args.check:
        check()
    else:
        generate()
