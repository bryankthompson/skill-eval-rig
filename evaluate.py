#!/usr/bin/env python3
"""Grade a finished run. Reads the JSON that `drive_interactive.py --json` (or any runner) writes,
runs the grader ensemble over each valid trial's transcript, denoises per question, applies an
explicit pass policy, computes per-cell reliability, and prints a slice report. Touches NONE of the
runtime — purely additive, downstream of the --json artifact.

  python3 evaluate.py --in run.json [--target dir-reply] [--floor 0.8]
"""
import argparse
import json

import graders as G
import label_model as LM
from slices import Slice, slice_report

# Two deterministic graders are live; SchemaGrader/LLMJudgeGrader abstain until configured (so the
# ensemble can grow without touching this list). Keep graders DIVERSE per question — see label_model.
ENSEMBLE = [G.CommandGrader(), G.NoErrorGrader(), G.SchemaGrader(), G.LLMJudgeGrader()]


def evaluate(run, graders=ENSEMBLE, target="dir-reply", floor=0.8):
    per_cell_votes, all_votes = [], []
    for cell in run["cells"]:
        votes = []
        for t in cell.get("trials", []):
            if not t.get("valid"):
                continue                      # validity guard already excluded infra failures
            tv = G.grade_transcript(t["transcript"], t, graders)
            votes.append(tv)
            all_votes.append(tv)
        per_cell_votes.append(votes)

    reliab = LM.estimate_reliability(all_votes)
    targets = {"command": target, "no_error": "ok"}   # explicit pass policy; tune per battery
    for cell, votes in zip(run["cells"], per_cell_votes):
        passes = [LM.pass_policy(LM.trial_verdict(v, reliab), targets) for v in votes]
        cell["reliability"] = LM.cell_reliability(passes)

    cells = run["cells"]
    slices = [Slice("arm:OLD", lambda c: c.get("arm") == "OLD"),
              Slice("arm:REVISED", lambda c: c.get("arm") == "REVISED"),
              Slice("kind:positive", lambda c: c.get("kind") == "positive"),
              Slice("kind:negative", lambda c: c.get("kind") == "negative")]
    return {"reliability": reliab, "cells": cells, "slices": slice_report(cells, slices, floor)}


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--in", dest="inp", required=True)
    ap.add_argument("--target", default="dir-reply")
    ap.add_argument("--floor", type=float, default=0.8)
    a = ap.parse_args()
    with open(a.inp) as fh:
        run = json.load(fh)
    rep = evaluate(run, target=a.target, floor=a.floor)
    print("estimated grader reliability:", {k: round(v, 2) for k, v in rep["reliability"].items()})
    for s in rep["slices"]:
        print(f"  {s['slice']:16} mean={s['mean_rate']} n={s['n_cells']}"
              + ("  <- below floor" if s["below_floor"] else ""))


if __name__ == "__main__":
    main()
