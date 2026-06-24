#!/usr/bin/env python3
"""Slice-based eval: carve cells into named subpopulations and surface those below a reliability
floor — the rig's manual cliff-finding (the activation cliff, the 0/5↔5/5 selection collapse) made
an automatic sweep. A Slice is a name + a predicate over a cell record (any dict carrying metadata
plus a 'reliability' block from label_model.cell_reliability). Define slices over whatever metadata
the run records: arm, model, prompt kind, tool, context-window, prompt language, …"""
from dataclasses import dataclass
from typing import Callable


@dataclass
class Slice:
    name: str
    predicate: Callable[[dict], bool]


def slice_report(cells, slices, floor=0.8):
    """cells: list of cell records, each with cell['reliability']['rate']. One row per slice: mean
    pass-rate over matching cells, n, and a below_floor flag (the failure map). Sorted worst-first."""
    rows = []
    for s in slices:
        matched = [c for c in cells if s.predicate(c)]
        rates = [c["reliability"]["rate"] for c in matched if "reliability" in c]
        mean = sum(rates) / len(rates) if rates else None
        rows.append({"slice": s.name, "n_cells": len(matched),
                     "mean_rate": (round(mean, 3) if mean is not None else None),
                     "below_floor": (mean is not None and mean < floor)})
    return sorted(rows, key=lambda r: (r["mean_rate"] is None, r["mean_rate"] or 0.0))
