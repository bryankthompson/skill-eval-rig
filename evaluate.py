#!/usr/bin/env python3
"""Grade a finished run. Reads the JSON that `drive_interactive.py --json` (or any runner) writes,
runs the grader ensemble over each valid trial's transcript, denoises per question, applies an
explicit pass policy, computes per-cell reliability, and prints a slice report. Touches NONE of the
runtime — purely additive, downstream of the --json artifact.

  python3 evaluate.py --in run.json [--target dir-reply] [--floor 0.8]
  python3 evaluate.py --in run.json --schemas tools.json   # activate the SchemaGrader
  python3 evaluate.py --in run.json --rubric judge.txt      # activate the LLMJudgeGrader (live claude -p)
"""
import argparse
import json

import graders as G
import label_model as LM
from slices import Slice, slice_report

def evaluate(run, graders=None, target="dir-reply", floor=0.8, targets=None):
    # Default to the two SAFE deterministic graders (never the live judge) — matches the CLI's bare
    # ensemble and avoids a shared-mutable default. Schema/judge are opt-in (see main()).
    graders = graders if graders is not None else [G.CommandGrader(), G.NoErrorGrader()]
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
    targets = targets or {"command": target, "no_error": "ok"}   # explicit pass policy; tune per battery
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
    ap.add_argument("--schemas", help="JSON file {tool_name: input_schema} → activates SchemaGrader")
    ap.add_argument("--rubric", help="text file with a judge rubric → activates LLMJudgeGrader (live)")
    ap.add_argument("--judge-model", dest="judge_model", help="model for the judge (default: CLI default)")
    a = ap.parse_args()
    with open(a.inp) as fh:
        run = json.load(fh)

    # Build the ensemble + pass policy from what's configured. Schema/judge join only when given a
    # schema set / rubric, so a bare run grades on the two deterministic graders and makes no calls.
    graders = [G.CommandGrader(), G.NoErrorGrader()]
    targets = {"command": a.target, "no_error": "ok"}
    if a.schemas:
        with open(a.schemas) as fh:
            graders.append(G.SchemaGrader(json.load(fh)))
        targets["schema"] = "ok"
    if a.rubric:
        with open(a.rubric) as fh:
            graders.append(G.LLMJudgeGrader(fh.read(), a.judge_model))
        targets["quality"] = "pass"

    rep = evaluate(run, graders=graders, floor=a.floor, targets=targets)
    print("estimated grader reliability:", {k: round(v, 2) for k, v in rep["reliability"].items()})
    for s in rep["slices"]:
        print(f"  {s['slice']:16} mean={s['mean_rate']} n={s['n_cells']}"
              + ("  <- below floor" if s["below_floor"] else ""))


if __name__ == "__main__":
    main()
