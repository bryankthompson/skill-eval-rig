#!/usr/bin/env python3
"""Score a directory of stream-json trial files produced by run_trials.sh.

Reports per condition (grouped by the model embedded in the filename):
  correct%      : the needle token appeared in the final answer
  right-file    : the intended file was Read (selection: --right-file; chain: last step)
  files_read    : avg number of reference files read (token-cost / blowup signal)
  read-depth    : whether reads were full vs partial (limit=) on the needle-bearing file
  nav           : whether the model fell back to Grep/Glob/ls instead of index routing
"""
import argparse, os, json, glob

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", required=True)
    ap.add_argument("--needle", required=True, help="token that proves correct retrieval")
    ap.add_argument("--right-file", default=None, help="basename of the file that should be read")
    args = ap.parse_args()

    groups = {}
    for f in sorted(glob.glob(os.path.join(args.dir, "*.jsonl"))):
        parts = os.path.basename(f)[:-6].split("__")
        model = parts[1] if len(parts) >= 2 else "?"
        final, reads, nav, partial = "", [], False, False
        for line in open(f):
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except Exception:
                continue
            if ev.get("type") == "result":
                final = ev.get("result", "") or ""
            msg = ev.get("message") or {}
            cont = msg.get("content") if isinstance(msg.get("content"), list) else []
            for c in cont:
                if not (isinstance(c, dict) and c.get("type") == "tool_use"):
                    continue
                nm, inp = c.get("name"), c.get("input", {})
                if nm == "Read":
                    fp = os.path.basename(inp.get("file_path", ""))
                    reads.append(fp)
                    if inp.get("limit"):
                        partial = True
                elif nm in ("Grep", "Glob"):
                    nav = True
                elif nm == "Bash" and any(x in inp.get("command", "") for x in ("grep", "ls ", "find ", "rg ", "cat ")):
                    nav = True
        hit = args.needle in final
        right = (args.right_file in reads) if args.right_file else None
        nref = len([r for r in reads if r.startswith(("t", "step"))])
        groups.setdefault(model, []).append((hit, right, nref, nav, partial))

    for model, rows in sorted(groups.items()):
        n = len(rows)
        c = sum(1 for h, *_ in rows if h)
        rf = sum(1 for _, r, *_ in rows if r) if args.right_file else None
        af = sum(x[2] for x in rows) / n if n else 0
        navc = sum(1 for x in rows if x[3])
        prt = sum(1 for x in rows if x[4])
        rfs = f" right-file={rf}/{n}" if args.right_file else ""
        print(f"{model}: correct={c}/{n}{rfs} avg_files_read={af:.1f} nav={navc}/{n} partial_reads={prt}/{n}")

if __name__ == "__main__":
    main()
