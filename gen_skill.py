#!/usr/bin/env python3
"""Generate ONE skill with reference files, for selection / chaining experiments.

Two modes:
  --mode selection : N flat reference files; the needle file holds the token; SKILL.md
                     routes to them with one of three index styles.
  --mode chain     : a step1->step2->...->stepD reference chain (needle in the last step);
                     SKILL.md points only at step1. Optionally make one step oversized.

Index styles (selection mode):
  good    : every file gets a discriminating one-line description; the needle's line
            semantically matches the intended query.
  uniform : every file (incl. the needle) gets the SAME generic description -> the index
            cannot disambiguate (this is the regime that breaks retrieval).
  nav     : SKILL.md gives no per-file index; it tells the model the files live in
            references/ and to list+read the relevant one (filesystem navigation).
"""
import argparse, os, sys

def write(p, s):
    os.makedirs(os.path.dirname(p), exist_ok=True)
    open(p, "w").write(s)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True, help="project dir (skill is written under <out>/.claude/skills/<name>)")
    ap.add_argument("--name", default="mega")
    ap.add_argument("--mode", choices=["selection", "chain"], default="selection")
    ap.add_argument("--files", type=int, default=100, help="selection: number of reference files")
    ap.add_argument("--needle-file", type=int, default=None, help="selection: 1-based index of the needle file (default ~middle)")
    ap.add_argument("--index", choices=["good", "uniform", "nav"], default="good")
    ap.add_argument("--needle", default="NEEDLE-TOKEN-001")
    ap.add_argument("--needle-desc", default="Aggressive maintenance profile for the high-write OLTP tier: background cleanup throughput cost limit.",
                    help="selection/good: the discriminating index line + content framing for the needle file")
    ap.add_argument("--distractor-desc", default="Generic database tier configuration profile.")
    ap.add_argument("--leaf-lines", type=int, default=40, help="lines of filler in the needle/leaf file before the token (depth); honored in BOTH selection and chain modes")
    ap.add_argument("--disjoint-body", action="store_true",
                    help="selection: write the needle body with a NEUTRAL heading (no query/index "
                         "terms) so the body offers no lexical grep handle — isolates routing-signal "
                         "(index) quality from body lexical-recoverability (H3/L1)")
    ap.add_argument("--chain-depth", type=int, default=5, help="chain mode: number of hops")
    ap.add_argument("--big-step", type=int, default=0, help="chain mode: make this step oversized (0=none)")
    ap.add_argument("--big-lines", type=int, default=2600)
    args = ap.parse_args()

    base = os.path.join(args.out, ".claude", "skills", args.name)
    refs = os.path.join(base, "references")

    if args.mode == "selection":
        needle_i = args.needle_file or (args.files // 2 + 1)
        for i in range(1, args.files + 1):
            fn = os.path.join(refs, f"t{i:04d}.md")
            if i == needle_i:
                body = ["# profile"] + [f"- note {k}" for k in range(args.leaf_lines)]
                # disjoint-body: a neutral heading so no query/index term appears in the body and
                # grep cannot recover the needle — the index is then the ONLY routing channel.
                heading = "reference entry" if args.disjoint_body else args.needle_desc
                body += ["", f"## {heading}", f"The documented value is {args.needle}."]
            else:
                body = [f"# topic {i}"] + [f"- {args.distractor_desc} variant {i}-{k} value V{(i*53+k)%9000}" for k in range(8)]
            write(fn, "\n".join(body) + "\n")
        # SKILL.md
        if args.index == "nav":
            skill = (f"---\nname: {args.name}\ndescription: Reference library across {args.files} profiles.\n---\n"
                     f"# {args.name}\nThe references/ directory holds {args.files} files named tNNNN.md. "
                     f"List that directory and read the most relevant file(s) for the question.\n")
        else:
            lines = []
            for i in range(1, args.files + 1):
                d = args.needle_desc if (i == needle_i and args.index == "good") else args.distractor_desc + f" {i}"
                lines.append(f"- [references/t{i:04d}.md](references/t{i:04d}.md) — {d}")
            skill = (f"---\nname: {args.name}\ndescription: Reference library across {args.files} profiles.\n---\n"
                     f"# {args.name}\nPick the single most relevant reference and read it.\n\n" + "\n".join(lines) + "\n")
        write(os.path.join(base, "SKILL.md"), skill)
        print(f"[selection] {args.name}: {args.files} files, needle=t{needle_i:04d}, index={args.index}, token={args.needle}")

    else:  # chain
        D = args.chain_depth
        for s in range(1, D):
            extra = "\n".join(f"- bulk line {k}" for k in range(args.big_lines)) if s == args.big_step else \
                    "\n".join(f"- procedural detail {s}.{k}" for k in range(20))
            write(os.path.join(refs, f"step{s}.md"),
                  f"# Step {s}\nContinue to the next step: [step{s+1}.md](step{s+1}.md)\n{extra}\n")
        write(os.path.join(refs, f"step{D}.md"),
              f"# Step {D}\n" + "\n".join(f"- final detail {k}" for k in range(args.leaf_lines)) + f"\n\nThe final token is {args.needle}.\n")
        write(os.path.join(base, "SKILL.md"),
              f"---\nname: {args.name}\ndescription: A {D}-step runbook. Walk it to the final step.\n---\n"
              f"# {args.name}\nBegin by reading [references/step1.md](references/step1.md); follow each step's pointer to the next, to the end.\n")
        print(f"[chain] {args.name}: depth={D}, big_step={args.big_step or 'none'}, token={args.needle}")

if __name__ == "__main__":
    main()
