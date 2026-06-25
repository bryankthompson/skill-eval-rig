#!/usr/bin/env python3
"""Public-hygiene regression guard.

This is a PUBLIC repo. It must carry no dangling internal pointers — references
to the operator's private tooling that an external cloner cannot resolve, or
that name a partner/incident. Such pointers leaked in once and were removed by
a dedicated de-internalization pass; before that, the property was enforced only
by reviewer attention, so it regressed silently. This test locks the property in
so a future edit — or a copy-paste from the private tooling repo — can't re-leak.

DENY is the set of internal tokens that were REMOVED for public release. It is
deliberately scoped to those exact tokens, NOT to the internal *vocabulary* that
legitimately REMAINS in each command stub's line-2 ``description:`` — that line is
the measured A/B stimulus (kept byte-identical) and may carry internal tool names
(outreach-db, asana-db) and policy refs (§3.A/§1.D) on purpose, so ``description:``
lines are exempt from the scan (see ``_is_exempt_line``). The line-3
``argument-hint`` and all prose/comments ARE scanned.
"""
import pathlib
import unittest

HERE = pathlib.Path(__file__).resolve().parent          # tests/
ROOT = HERE.parent                                       # repo root
SELF = pathlib.Path(__file__).resolve()                  # this file lists the tokens as data → exclude

# Internal pointers removed for public release — must not reappear.
DENY = [
    "mcp-local-directory",        # the operator's private repo name
    "#2327",                       # a PR number in that private repo
    "id 1441",                     # an opaque internal tracker id (incl. the bare "(id 1441)" form)
    "reference_skill_eval_rig",    # an internal memory-file name
    "Tableau",                     # a partner name tied to an internal incident reference
    "status-db",                   # an internal tool name
]
DENY_CI = ["miro"]                 # a partner name — match case-insensitively

# Public-facing tree to scan (matches the de-internalization pass's footprint):
# everything under experiments/activation/, the top-level findings/readme, and tests/.
SCAN_DIRS = [ROOT / "experiments" / "activation", ROOT / "tests"]
SCAN_FILES = [ROOT / "FINDINGS.md", ROOT / "README.md"]
SCAN_SUFFIXES = {".md", ".py"}


def _is_exempt_line(line: str) -> bool:
    # The measured A/B stimulus — kept byte-identical, may legitimately carry
    # internal vocabulary. Only the `description:` frontmatter line is exempt;
    # `argument-hint:` (a cross-arm constant) and everything else is scanned.
    return line.lstrip().startswith("description:")


def _iter_files():
    seen = set()
    for d in SCAN_DIRS:
        if d.is_dir():
            for p in d.rglob("*"):
                if p.is_file() and p.suffix in SCAN_SUFFIXES:
                    seen.add(p.resolve())
    for f in SCAN_FILES:
        if f.is_file():
            seen.add(f.resolve())
    seen.discard(SELF)             # this guard necessarily contains the tokens as data
    return sorted(seen)


class PublicHygiene(unittest.TestCase):
    def test_no_internal_tokens_leak(self):
        offenders = []
        for p in _iter_files():
            try:
                text = p.read_text(encoding="utf-8")
            except Exception:
                continue
            rel = p.relative_to(ROOT)
            for lineno, line in enumerate(text.splitlines(), 1):
                if _is_exempt_line(line):
                    continue
                low = line.lower()
                for tok in DENY:
                    if tok in line:
                        offenders.append(f"{rel}:{lineno}: {tok!r} in: {line.strip()[:80]}")
                for tok in DENY_CI:
                    if tok in low:
                        offenders.append(f"{rel}:{lineno}: {tok!r} (case-insensitive) in: {line.strip()[:80]}")
        self.assertEqual(
            offenders, [],
            "internal token(s) leaked into the public tree:\n" + "\n".join(offenders),
        )


if __name__ == "__main__":
    unittest.main()
