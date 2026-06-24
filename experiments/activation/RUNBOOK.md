# Activation experiment (INTERACTIVE — cannot be done headless)

> **Scope:** this is the **synthetic SKILL auto-activation** cliff probe (the `vacuum-expert` /
> `ctx-policy-71` needle-`4200` budget/naming experiment). **Sibling:** `dir-reply/RUNBOOK.md`
> covers **command** (slash-command) auto-activation — the `/dir-reply` OLD-vs-REVISED A/B.

`claude -p` resolves skills only via explicit `/skill-name`, so it never exercises *auto*-activation. To test whether a skill auto-fires — and whether that survives the listing budget — you must drive **interactive** sessions. This is the one axis automated CI cannot cover.

**Needle:** the `vacuum-expert` (or opaque `ctx-policy-71`) skill's reference is the only place the value `4200` exists, so a `4200` answer proves the skill fired and was read. Any other number ⇒ it did not fire.

## Build the conditions
```
python3 gen_listing.py --out /tmp/act/L0_under   --fillers 8  --filler-desc-chars 20            # under budget
python3 gen_listing.py --out /tmp/act/L2_over    --fillers 70 --filler-desc-chars 1000          # over budget, fat fillers
# Force the NEEDLE itself to be dropped: opaque name + fat description + tiny budget
python3 gen_listing.py --out /tmp/act/L4_dropped --needle-name ctx-policy-71 --needle-desc-chars 1000 \
        --fillers 3 --filler-desc-chars 20 --budget-fraction 0.0001
# Control for L4: same opaque name, description PRESENT (normal budget)
python3 gen_listing.py --out /tmp/act/L4_present --needle-name ctx-policy-71 --fillers 3 --filler-desc-chars 20
```
(Edit `tasks.json` cwd paths to match, then use it as a VS Code launcher: Run Task → pick a level.)

## Protocol — run in EACH level's fresh interactive session
**ORDER MATTERS.** Do Step 1 *before* `/doctor` or `/skills` — running those first dumps skill names into the conversation and primes the model, contaminating the auto-activation measurement.

1. **Auto-activation (the measurement).** First input, a *natural* prompt (NOT `/vacuum-expert`):
   `What autovacuum_vacuum_cost_limit value should I set for a high-write OLTP Postgres tier?`
   Record: did a skill visibly load? Was the answer `4200` (fired) or some other number (did not fire)?
2. **Drop status.** Run `/doctor` (lists which descriptions were dropped) and `/skills`. Record whether the needle skill's description was dropped.
3. **Explicit-invoke control.** `/<needle-name> <same question>` → should answer `4200` at every level (proves only *discovery* was lost, not the skill).

## What we measured (Opus 4.8, 1M context)
| Condition | Needle name | Description in listing | Auto-fires? |
|---|---|---|---|
| L0_under | vacuum-expert | present | ✅ 4200 |
| L2_over | vacuum-expert | **dropped** (fat fillers culled first... then it) | ✅ 4200 — **name carried it** |
| L4_present | ctx-policy-71 (opaque) | present | ✅ 4200 — description carried it |
| L4_dropped | ctx-policy-71 (opaque) | **dropped** | ❌ answered ~2000 from general knowledge — **silent miss** |

**Boundary:** a skill goes dark for auto-discovery only when its description is dropped **and** its name carries no routing signal — and it then fails *silently* (plausible, wrong, no error). Explicit `/invoke` always worked. Lessons: name skills descriptively; keep descriptions lean (the budget culls the fattest first).
