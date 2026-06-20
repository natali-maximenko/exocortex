#!/usr/bin/env python3
# see WP-415 (IWE translation pipeline: RU FMT-exocortex-template → EN iwesys)
"""IWE document translator: RU source → EN candidate via Claude API.

Usage:
    # Translate files to output directory
    python3 scripts/translate.py --mode=translate --output-dir ../en-out docs/README.md

    # Discover untranslated terms (writes CSV to stdout)
    python3 scripts/translate.py --mode=discover docs/README.md
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
import time
from pathlib import Path

import anthropic
import yaml

DEFAULT_MODEL = "claude-sonnet-4-6"

_SCRIPT_ROOT = Path(__file__).parent.parent  # FMT-exocortex-template root
DEFAULT_STYLE = _SCRIPT_ROOT / "translation" / "en-doc-style.md"
DEFAULT_MANIFEST = _SCRIPT_ROOT / "translation-manifest.yaml"
DEFAULT_GLOSSARY = _SCRIPT_ROOT / "translation" / "glossary-v0.1.csv"

# ~180K tokens at 4 chars/token — skip files above this
MAX_PROMPT_CHARS = 720_000
# Maximum lines to scan for closing --- in frontmatter (Critical fix #1)
MAX_FM_SCAN_LINES = 50

FENCED_BLOCK_RE = re.compile(r"```.*?```", re.DOTALL)
INLINE_CODE_RE = re.compile(r"`[^`\n]+`")
# Discover-mode patterns: ALL-CAPS 2+ chars (High fix #1) and mixed-case 4+
CYRILLIC_ALL_CAPS_RE = re.compile(r"[А-ЯЁ]{2,}")
CYRILLIC_MIXED_RE = re.compile(r"[а-яёА-ЯЁ]{4,}")


# ---------------------------------------------------------------------------
# Frontmatter parsing
# ---------------------------------------------------------------------------


def parse_frontmatter(text: str) -> tuple[dict, str]:
    """Extract YAML frontmatter. Returns (meta, body).

    On any parse failure returns ({}, full_text) — never raises (Critical fix #2).
    Scans at most MAX_FM_SCAN_LINES lines for the closing --- (Critical fix #1).
    """
    lines = text.split("\n")
    if not lines or lines[0].strip() != "---":
        return {}, text
    closing: int | None = None
    for i, line in enumerate(lines[1:MAX_FM_SCAN_LINES], start=1):
        if line.strip() == "---":
            closing = i
            break
    if closing is None:
        return {}, text
    fm_text = "\n".join(lines[1:closing])
    body = "\n".join(lines[closing + 1:])
    try:
        meta = yaml.safe_load(fm_text) or {}
    except yaml.YAMLError:
        return {}, text
    if not isinstance(meta, dict):
        return {}, text
    return meta, body


def serialize_frontmatter(meta: dict) -> str:
    """Serialize meta dict back to a YAML frontmatter block."""
    return "---\n" + yaml.dump(meta, allow_unicode=True, default_flow_style=False) + "---\n"


# ---------------------------------------------------------------------------
# Code stripping and ASCII guard
# ---------------------------------------------------------------------------


def strip_code_for_guard(text: str) -> str:
    """Remove fenced and inline code blocks before Cyrillic checks (Critical fix #3)."""
    text = FENCED_BLOCK_RE.sub("", text)
    text = INLINE_CODE_RE.sub("", text)
    return text


def ascii_guard(body: str, meta: dict, translate_keys: list[str]) -> list[str]:
    """Return list of Cyrillic violations after translation.

    Positive-list approach: only checks values for keys in translate_keys (High fix #2).
    """
    violations: list[str] = []
    clean_body = strip_code_for_guard(body)
    for i, line in enumerate(clean_body.split("\n"), start=1):
        if re.search(r"[а-яёА-ЯЁ]", line):
            violations.append(f"body:{i}: {line.rstrip()}")
    for key in translate_keys:
        value = meta.get(key)
        if isinstance(value, str) and re.search(r"[а-яёА-ЯЁ]", value):
            violations.append(f"frontmatter:{key}: {value}")
    return violations


# ---------------------------------------------------------------------------
# API call with retry
# ---------------------------------------------------------------------------


def translate_with_retry(
    client: anthropic.Anthropic,
    system_prompt: str,
    user_content: str,
    model: str,
    max_retries: int = 5,
) -> str:
    """Call Claude API with exponential backoff on rate-limit errors (Critical fix #4)."""
    delay = 2.0
    for attempt in range(max_retries):
        try:
            response = client.messages.create(
                model=model,
                max_tokens=8192,
                system=system_prompt,
                messages=[{"role": "user", "content": user_content}],
            )
            return response.content[0].text
        except anthropic.RateLimitError:
            if attempt == max_retries - 1:
                raise
            time.sleep(delay)
            delay *= 2
        except anthropic.APIStatusError as e:
            if e.status_code == 429:
                if attempt == max_retries - 1:
                    raise
                time.sleep(delay)
                delay *= 2
            else:
                raise
    raise RuntimeError("unreachable")  # pragma: no cover


# ---------------------------------------------------------------------------
# Response parsing (XML-marker split)
# ---------------------------------------------------------------------------


def _parse_translation_response(
    response: str,
    fm_values: dict,
    translate_keys: list[str],
) -> tuple[dict, str]:
    """Split combined FM+body LLM response. Returns (translated_fm_dict, body_text).

    If LLM did not use XML markers (fallback), uses full response as body.
    """
    if not fm_values:
        return {}, response

    fm_translated: dict[str, str] = {}
    fm_match = re.search(
        r"<frontmatter_values>(.*?)</frontmatter_values>", response, re.DOTALL
    )
    if fm_match:
        for line in fm_match.group(1).strip().split("\n"):
            if ":" in line:
                k, _, v = line.partition(":")
                k, v = k.strip(), v.strip()
                if k in translate_keys:
                    fm_translated[k] = v

    body_match = re.search(r"<body>(.*?)</body>", response, re.DOTALL)
    if body_match:
        body_text = body_match.group(1)
        # Strip exactly one leading newline produced by the XML marker format
        if body_text.startswith("\n"):
            body_text = body_text[1:]
    else:
        # LLM did not use markers — treat entire response as body
        body_text = response

    return fm_translated, body_text


# ---------------------------------------------------------------------------
# File translation
# ---------------------------------------------------------------------------


def translate_file(
    file_path: Path,
    system_prompt: str,
    translate_keys: list[str],
    client: anthropic.Anthropic,
    model: str,
) -> tuple[str, list[str]]:
    """Translate a single file. Returns (translated_text, violations).

    violations is empty on success, or contains 'file_too_large: ...' on overflow.
    """
    text = file_path.read_text(encoding="utf-8")
    meta, body = parse_frontmatter(text)

    fm_values: dict[str, str] = {}
    if meta and translate_keys:
        fm_values = {
            k: meta[k]
            for k in translate_keys
            if k in meta and isinstance(meta[k], str)
        }

    # Single LLM call for frontmatter + body (High fix #3)
    parts: list[str] = []
    if fm_values:
        fm_section = "\n".join(f"{k}: {v}" for k, v in fm_values.items())
        parts.append(
            "Translate the following frontmatter field values "
            "(keep key names unchanged, return in same key: value format):\n"
            f"<frontmatter_values>\n{fm_section}\n</frontmatter_values>"
        )
    parts.append(f"Translate the following document body:\n<body>\n{body}\n</body>")
    user_content = "\n\n".join(parts)

    # Guard against context overflow before making the API call (Critical fix #5)
    total_chars = len(system_prompt) + len(user_content)
    if total_chars > MAX_PROMPT_CHARS:
        approx_k = total_chars // 4 // 1000
        return "", [
            f"file_too_large: {total_chars} chars (~{approx_k}k tokens), "
            f"limit ~{MAX_PROMPT_CHARS // 4 // 1000}k tokens"
        ]

    raw = translate_with_retry(client, system_prompt, user_content, model)
    fm_translated, en_body = _parse_translation_response(raw, fm_values, translate_keys)

    en_meta = dict(meta)
    for k, v in fm_translated.items():
        en_meta[k] = v

    violations = ascii_guard(en_body, en_meta, translate_keys)

    result = (serialize_frontmatter(en_meta) if en_meta else "") + en_body
    return result, violations


# ---------------------------------------------------------------------------
# Git root detection
# ---------------------------------------------------------------------------


def _find_repo_root(start: Path) -> Path:
    """Walk up from start until a .git directory is found (High fix #4)."""
    current = start.resolve()
    for _ in range(10):
        if (current / ".git").exists():
            return current
        parent = current.parent
        if parent == current:
            break
        current = parent
    return start.resolve()


# ---------------------------------------------------------------------------
# Glossary and system prompt
# ---------------------------------------------------------------------------


def _load_glossary(glossary_path: Path) -> dict[str, str]:
    """Load CSV glossary. Returns {ru_term: en_term}."""
    glossary: dict[str, str] = {}
    if not glossary_path.exists():
        return glossary
    with glossary_path.open(encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            ru = (row.get("term_ru") or "").strip()
            en = (row.get("term_en") or "").strip()
            if ru and en:
                glossary[ru] = en
    return glossary


def _build_system_prompt(style_path: Path, glossary: dict, manifest: dict) -> str:
    """Assemble three-layer system prompt: style + glossary + exclusions."""
    style_text = style_path.read_text(encoding="utf-8") if style_path.exists() else ""

    exclusions = manifest.get("exclusions", {})
    id_patterns = exclusions.get("id_patterns", [])
    proper_names = exclusions.get("proper_names", [])

    glossary_lines = "\n".join(f"  {ru} → {en}" for ru, en in glossary.items())
    id_pattern_lines = "\n".join(f"  - {p}" for p in id_patterns)
    proper_name_lines = "\n".join(f"  - {n}" for n in proper_names)

    return (
        "You are a technical translator converting IWE documentation from Russian to English.\n\n"
        f"# Style Rules\n{style_text}\n\n"
        f"# Glossary (use these translations exactly)\n{glossary_lines}\n\n"
        "# Do Not Translate\n"
        f"## Identifier patterns (keep verbatim)\n{id_pattern_lines}\n\n"
        f"## Proper nouns (keep verbatim)\n{proper_name_lines}\n\n"
        "# Output Format\n"
        "When given <frontmatter_values> and <body> sections, "
        "return them in the same XML-tag structure.\n"
        "When given only a <body> section, return only the translated body (no XML tags).\n"
        "Do not add explanations or commentary — return only the translated content."
    )


# ---------------------------------------------------------------------------
# Discover mode
# ---------------------------------------------------------------------------


def _record_term(
    term: str,
    line: str,
    glossary_keys: set,
    proper_names: set,
    id_patterns: list,
    term_freq: dict,
    term_context: dict,
) -> None:
    """Record a candidate term if not already known."""
    if term in glossary_keys or term in proper_names:
        return
    for pat in id_patterns:
        if pat.fullmatch(term):
            return
    if term not in term_freq:
        term_freq[term] = 0
        term_context[term] = line.strip()
    term_freq[term] += 1


def run_discover(args: argparse.Namespace, manifest: dict, glossary: dict) -> int:
    """Find Cyrillic terms not in glossary. Writes CSV to stdout."""
    exclusions = manifest.get("exclusions", {})
    id_patterns = [re.compile(p) for p in exclusions.get("id_patterns", [])]
    proper_names = set(exclusions.get("proper_names", []))
    glossary_keys = set(glossary.keys())

    term_freq: dict[str, int] = {}
    term_context: dict[str, str] = {}

    for file_arg in args.files:
        file_path = Path(file_arg)
        if not file_path.exists():
            print(f"# WARNING: file not found: {file_path}", file=sys.stderr)
            continue
        text = file_path.read_text(encoding="utf-8")
        clean = strip_code_for_guard(text)
        for line in clean.split("\n"):
            # ALL-CAPS Cyrillic 2+ chars (High fix #1: catches abbreviations like МИМ)
            for m in CYRILLIC_ALL_CAPS_RE.finditer(line):
                _record_term(
                    m.group(), line, glossary_keys, proper_names,
                    id_patterns, term_freq, term_context,
                )
            # Mixed-case 4+ chars; skip pure ALL-CAPS already matched above
            for m in CYRILLIC_MIXED_RE.finditer(line):
                if not CYRILLIC_ALL_CAPS_RE.fullmatch(m.group()):
                    _record_term(
                        m.group(), line, glossary_keys, proper_names,
                        id_patterns, term_freq, term_context,
                    )

    writer = csv.writer(sys.stdout)
    writer.writerow(["term_ru", "frequency", "example_context"])
    for term, freq in sorted(term_freq.items(), key=lambda x: -x[1]):
        if freq >= 2:
            writer.writerow([term, freq, term_context[term][:120]])
    return 0


# ---------------------------------------------------------------------------
# Translate mode
# ---------------------------------------------------------------------------


def run_translate(args: argparse.Namespace, manifest: dict, glossary: dict) -> int:
    """Translate files and write output to --output-dir."""
    if not args.output_dir:
        print("ERROR: --output-dir is required for translate mode", file=sys.stderr)
        return 1

    style_path = Path(args.style)
    output_dir = Path(args.output_dir)
    translate_keys: list[str] = manifest.get("frontmatter_translate_keys", [])

    client = anthropic.Anthropic()
    system_prompt = _build_system_prompt(style_path, glossary, manifest)
    model: str = args.model

    repo_root = _find_repo_root(Path.cwd())
    exit_code = 0

    for file_arg in args.files:
        file_path = Path(file_arg).resolve()
        # Mirror repo directory structure in output (High fix #4)
        try:
            rel = file_path.relative_to(repo_root)
        except ValueError:
            rel = Path(file_path.name)

        out_path = output_dir / rel
        out_path.parent.mkdir(parents=True, exist_ok=True)

        print(f"Translating: {rel}", file=sys.stderr)
        translated, violations = translate_file(
            file_path, system_prompt, translate_keys, client, model
        )

        if violations and violations[0].startswith("file_too_large"):
            print(f"  SKIP {violations[0]}", file=sys.stderr)
            # Write a marker file so CI can detect skipped files
            out_path.write_text(
                f"# TRANSLATION SKIPPED\n# {violations[0]}\n", encoding="utf-8"
            )
            continue

        out_path.write_text(translated, encoding="utf-8")

        if violations:
            print(f"  WARN ASCII-guard violations in {rel}:", file=sys.stderr)
            for v in violations:
                print(f"    {v}", file=sys.stderr)
            exit_code = 2
        else:
            print(f"  OK", file=sys.stderr)

    return exit_code


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(
        description="IWE document translator (RU → EN). WP-415 translation pipeline."
    )
    parser.add_argument(
        "--mode",
        choices=["translate", "discover"],
        default="translate",
        help="translate: call Claude API and write output; "
             "discover: find untranslated terms (CSV to stdout)",
    )
    parser.add_argument(
        "--manifest",
        default=str(DEFAULT_MANIFEST),
        help="Path to translation-manifest.yaml",
    )
    parser.add_argument(
        "--glossary",
        default=str(DEFAULT_GLOSSARY),
        help="Path to glossary CSV (term_ru, term_en columns)",
    )
    parser.add_argument(
        "--style",
        default=str(DEFAULT_STYLE),
        help="Path to EN doc style rules markdown",
    )
    parser.add_argument(
        "--model",
        default=DEFAULT_MODEL,
        help=f"Claude model ID (default: {DEFAULT_MODEL})",
    )
    parser.add_argument(
        "--output-dir",
        default=None,
        help="Output directory for translated files (translate mode only)",
    )
    parser.add_argument(
        "files",
        nargs="+",
        help="Files to process",
    )
    args = parser.parse_args()

    manifest_path = Path(args.manifest)
    if not manifest_path.exists():
        print(f"ERROR: manifest not found: {manifest_path}", file=sys.stderr)
        return 1

    with manifest_path.open(encoding="utf-8") as f:
        manifest = yaml.safe_load(f) or {}

    glossary = _load_glossary(Path(args.glossary))

    if args.mode == "discover":
        return run_discover(args, manifest, glossary)
    return run_translate(args, manifest, glossary)


if __name__ == "__main__":
    sys.exit(main())
