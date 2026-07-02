#!/usr/bin/env python3
"""Offline pins for gen_memory.py — the memory-recall probe generator's slug rule + SAFETY GUARDS
+ match-arm disjointness assertion. No live claude; runs under `make test` like the other tests.

The safety guards are the load-bearing bit: gen_memory MUST refuse to write outside a temp sandbox
or into a live memory store BEFORE any file is created (a bug there could clobber the real store)."""
import os
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "experiments", "activation"))
import gen_memory as gm  # noqa: E402
from drive_interactive import slug_for  # noqa: E402


def run(proj, name, match, desc, prompt, extra=None):
    args = [sys.executable, os.path.join(ROOT, "gen_memory.py"),
            "--proj", proj, "--name", name, "--match", match, "--desc", desc,
            "--prompt", prompt, "--body-nonce", "MEMBODYZXCV", "--desc-nonce", "MEMDESCQWRT",
            "--ctrl-nonce", "MEMCTRLAAAA", "--behavioral"] + (extra or [])
    return subprocess.run(args, capture_output=True, text=True)


class TestSlugRule(unittest.TestCase):
    def test_nonalnum_all_collapse(self):
        # every non-alnum -> '-', on the REALPATH (INV-CLAUDE-PROJECT-SLUG-NONALNUM-DASH)
        self.assertEqual(gm.slug_for("/tmp"), slug_for("/tmp"))
        s = slug_for("/private/tmp/memrecall.AbC-1/wd")
        self.assertNotIn(".", s)
        self.assertNotIn("/", s)
        self.assertTrue(s.startswith("-private-tmp-"))


class TestSafetyGuards(unittest.TestCase):
    def test_refuses_nontmp_proj(self):
        # GUARD 1 (temp-root): a non-tmp path is refused before any write.
        nontmp = os.path.dirname(ROOT)  # the parent of the repo checkout — a non-tmp path
        r = run(nontmp, "p", "none", "medieval falconry glossary", "kubernetes autoscaling")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("not under a temp root", r.stdout + r.stderr)  # specifically guard 1

    def test_allow_unsafe_does_not_bypass_clobber_guard(self):
        # GUARD 2 (no-clobber): --allow-unsafe-nontmp waives ONLY the temp-root guard; a dir that
        # already holds a foreign .md (no probe marker) is STILL refused — the generic live-store
        # defense, no hardcoded slug names.
        with tempfile.TemporaryDirectory(dir="/tmp") as sb:
            proj = os.path.join(sb, "wd")
            mdir = gm.memory_dir_for(proj)
            os.makedirs(mdir, exist_ok=True)
            open(os.path.join(mdir, "real-memory.md"), "w").close()  # foreign .md, NO marker
            try:
                r = run(proj, "p", "none", "medieval falconry", "kubernetes autoscaling",
                        extra=["--allow-unsafe-nontmp"])
                self.assertNotEqual(r.returncode, 0)
                self.assertIn("REFUSING", r.stdout + r.stderr)
            finally:
                import shutil
                shutil.rmtree(os.path.dirname(mdir), ignore_errors=True)

    def test_refuses_clobber_unmarked_dir(self):
        with tempfile.TemporaryDirectory(dir="/tmp") as sb:
            proj = os.path.join(sb, "wd")
            mdir = gm.memory_dir_for(proj)
            os.makedirs(mdir, exist_ok=True)
            open(os.path.join(mdir, "real-memory.md"), "w").close()  # foreign .md, NO marker
            try:
                r = run(proj, "p", "none", "medieval falconry", "kubernetes autoscaling")
                self.assertNotEqual(r.returncode, 0)
                self.assertIn("REFUSING", r.stdout + r.stderr)
            finally:
                import shutil
                shutil.rmtree(os.path.dirname(mdir), ignore_errors=True)  # whole <slug>/ dir, not just memory/

    def test_happy_path_plants_marker_ctrl_and_body(self):
        with tempfile.TemporaryDirectory(dir="/tmp") as sb:
            proj = os.path.join(sb, "wd")
            mdir = gm.memory_dir_for(proj)
            try:
                r = run(proj, "probe-lex", "lexical", "kubernetes pod autoscaling tuning",
                        "kubernetes pod autoscaling")
                self.assertEqual(r.returncode, 0, r.stderr)
                self.assertTrue(os.path.exists(os.path.join(mdir, gm.MARKER)))
                memmd = open(os.path.join(mdir, "MEMORY.md")).read()
                self.assertIn("MEMCTRLAAAA", memmd)          # positive control in always-load
                self.assertNotIn("probe-lex", memmd)          # index does NOT link the probe
                probe = open(os.path.join(mdir, "probe-lex.md")).read()
                self.assertIn("MEMDESCQWRT", probe)           # desc nonce
                self.assertIn("MEMBODYZXCV", probe)           # behavioral body nonce
                self.assertIn("French", probe)                # behavioral instruction
            finally:
                import shutil
                shutil.rmtree(os.path.dirname(mdir), ignore_errors=True)  # whole <slug>/ dir, not just memory/


class TestDisjointnessAssertion(unittest.TestCase):
    def test_semantic_with_shared_token_refused(self):
        with tempfile.TemporaryDirectory(dir="/tmp") as sb:
            r = run(os.path.join(sb, "wd"), "p", "semantic",
                    "kubernetes scaling notes", "kubernetes autoscaling")  # shares 'kubernetes'
            self.assertEqual(r.returncode, 3, "disjointness violation is a fixture error → exit 3 (docstring contract)")
            self.assertIn("ZERO shared tokens", r.stdout + r.stderr)

    def test_lexical_without_overlap_refused(self):
        with tempfile.TemporaryDirectory(dir="/tmp") as sb:
            r = run(os.path.join(sb, "wd"), "p", "lexical",
                    "medieval falconry glossary", "kubernetes autoscaling")  # no shared token
            self.assertEqual(r.returncode, 3, "disjointness violation is a fixture error → exit 3 (docstring contract)")
            self.assertIn("shares NO token", r.stdout + r.stderr)

    def test_semantic_disjoint_accepted(self):
        with tempfile.TemporaryDirectory(dir="/tmp") as sb:
            proj = os.path.join(sb, "wd")
            mdir = gm.memory_dir_for(proj)
            try:
                # 'container orchestration elastic replica' — meaning-overlap, zero shared words.
                r = run(proj, "p", "semantic", "container orchestration elastic replica scaling",
                        "kubernetes pod autoscaling")
                self.assertEqual(r.returncode, 0, r.stderr)
            finally:
                import shutil
                shutil.rmtree(os.path.dirname(mdir), ignore_errors=True)  # whole <slug>/ dir, not just memory/


class TestSentinelValidation(unittest.TestCase):
    def test_hyphenated_or_numeric_nonce_refused(self):
        with tempfile.TemporaryDirectory(dir="/tmp") as sb:
            args = [sys.executable, os.path.join(ROOT, "gen_memory.py"),
                    "--proj", os.path.join(sb, "wd"), "--name", "p", "--match", "none",
                    "--desc", "medieval falconry", "--prompt", "kubernetes",
                    "--body-nonce", "MEM-BODY", "--ctrl-nonce", "MEMCTRLAAAA"]  # hyphen -> invalid
            r = subprocess.run(args, capture_output=True, text=True)
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("hyphen-free", r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main()
