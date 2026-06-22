#!/usr/bin/env python3
"""Generate a SKILL LISTING (one needle skill + N filler skills) for budget / activation
experiments. The needle skill's reference file holds the token, so a correct answer proves
the skill was discovered and used.

Knobs that matter:
  --fillers N             how many filler skills to pad the listing with
  --filler-desc-chars C   length of each filler description (cost; the budget cull is
                          cheapest-kept / most-expensive-dropped, so big fillers drop first)
  --needle-name NAME      descriptive ('vacuum-expert') vs opaque ('ctx-policy-71') — this is
                          the axis that decides whether a dropped-description skill still fires
  --needle-desc-chars C   make the needle's own description fat to force IT to be the drop target
  --budget-fraction F     write .claude/settings.json with skillListingBudgetFraction=F
                          (default: omit -> harness default 0.01 = 1% of context)
"""
import argparse, os, json

VAC_DESC = ("Autovacuum and vacuum cost-limit tuning for high-write OLTP Postgres databases. "
            "Use for autovacuum_vacuum_cost_limit, vacuum cost delay, and maintenance-throughput questions. ")
BIG = ("Operate configure monitor and troubleshoot this subsystem including provisioning capacity failover "
       "replication backup restore encryption IAM parameter groups maintenance windows upgrades performance "
       "tuning slow queries connection pooling cost optimization disaster recovery and migration runbooks. ")

def write(p, s):
    os.makedirs(os.path.dirname(p), exist_ok=True); open(p, "w").write(s)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--needle-name", default="vacuum-expert")
    ap.add_argument("--needle", default="4200")
    ap.add_argument("--needle-desc-chars", type=int, default=0, help="0 = lean default desc; >0 = pad to this many chars")
    ap.add_argument("--fillers", type=int, default=8)
    ap.add_argument("--filler-desc-chars", type=int, default=20)
    ap.add_argument("--budget-fraction", type=float, default=None)
    args = ap.parse_args()

    sk = os.path.join(args.out, ".claude", "skills")
    # needle skill
    desc = VAC_DESC if args.needle_desc_chars == 0 else (VAC_DESC * 10)[:args.needle_desc_chars]
    write(os.path.join(sk, args.needle_name, "SKILL.md"),
          f"---\nname: {args.needle_name}\ndescription: {desc}\n---\n# {args.needle_name}\n"
          f"When asked about autovacuum cost limits or vacuum tuning, read references/autovacuum.md and report the exact value.\n")
    write(os.path.join(sk, args.needle_name, "references", "autovacuum.md"),
          "# Autovacuum tuning\n" + "\n".join(f"- guidance {i}" for i in range(30)) +
          f"\n\n## High-write OLTP tier\nThe documented autovacuum_vacuum_cost_limit for this tier is {args.needle}.\n")
    # fillers
    for i in range(1, args.fillers + 1):
        fd = (BIG * 5)[:args.filler_desc_chars] if args.filler_desc_chars > 40 else f"Unrelated filler topic {i}."
        write(os.path.join(sk, f"filler-{i:03d}", "SKILL.md"),
              f"---\nname: filler-{i:03d}\ndescription: Topic {i:03d}: {fd}\n---\n# filler {i}\nReply OK.\n")
    if args.budget_fraction is not None:
        write(os.path.join(args.out, ".claude", "settings.json"),
              json.dumps({"skillListingBudgetFraction": args.budget_fraction}) + "\n")
    print(f"listing: needle={args.needle_name} (desc {len(desc)} chars), {args.fillers} fillers @ "
          f"{args.filler_desc_chars} chars, budget_fraction={args.budget_fraction or 'default(0.01)'}")

if __name__ == "__main__":
    main()
