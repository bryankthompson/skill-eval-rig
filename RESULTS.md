# Measured baseline

> This is the original **structural** baseline (selection/chaining/budget/activation). The
> full map — adversarial safety, the Opus/Haiku asymmetry, instruction precedence, cross-skill
> handoff, and the scale-dodge — is in **`FINDINGS.md`**; the injection surface in **`SECURITY.md`**.


Captured 2026-06-22 · `claude` CLI **2.1.185** · models **claude-opus-4-8 (1M context)** and **claude-haiku-4-5** · ~110 trials, N=4–5 per cell.

## Budget (structural, headless)
The always-loaded skill listing is capped by `skillListingBudgetFraction` (default **1%** of context), expressed in **characters**:

| Skills | Listing chars | Over 30K budget? |
|---|---|---|
| 73 | ~12,000 | no |
| 94 | 35,803 | yes → truncation |
| 140 | 82,999 | yes → heavy truncation |

Budget measured at **~30,000 chars on the 1M-context model** (≈1% of context). It scales with the context window, so on a 200K-context model it is ~6× smaller — which is why a ~13-skill plugin can already consume ~73% of the budget there. Over budget, descriptions are dropped **most-expensive-first** (lean descriptions are very hard to evict); **skill names are always retained**.

## Chaining (headless) — never broke
| Depth | Extra | Opus | Haiku | Reads |
|---|---|---|---|---|
| 2 hops | 271-line leaf | 5/5 | 4/4 | full |
| 5 hops | — | 4/4 | 4/4 | full |
| 10 hops | 2,600-line mid-chain file | 4/4 | 4/4 | full (offset/grep self-correct on oversize) |

The spec's "nested reference → `head -100` partial read → incomplete info" failure did not reproduce.

## Selection (headless) — count is fine; routing signal is the line
| Files | Index | Opus | Haiku | Files read |
|---|---|---|---|---|
| 100 | good | 5/5 | 5/5 | 1 |
| 300 | good (ambiguous distractors) | 4/4 | 4/4 | 1 |
| 1000 | good | 3/4 | 4/4 | ~1 |
| **500** | **uniform + lexically-disjoint query** | **0/5** | **0/5** | gave up / wrong |
| 500 | same corpus, **one discriminating index line** | **5/5** | — | 1 |

The decisive contrast: identical 500-file corpus and query, flip only the needle's index entry quality → **0/5 → 5/5** on both models. The constraint is description/index quality, not file count.

## Activation (interactive only) — the silent cliff
| Needle name | Description in listing | Auto-fires? |
|---|---|---|
| `vacuum-expert` (descriptive) | present | ✅ 4200 |
| `vacuum-expert` (descriptive) | **dropped** | ✅ 4200 — name carried discovery |
| `ctx-policy-71` (opaque) | present | ✅ 4200 — description carried discovery |
| `ctx-policy-71` (opaque) | **dropped** | ❌ answered ~2000 from general knowledge — **silent miss** |

A skill goes dark for auto-discovery only when its description is dropped **and** its name carries no routing signal — and it then fails *silently* (plausible, confident, wrong; no error). Explicit `/invoke` worked in every condition.

## So what
- The number/size/nesting of reference files are **not** the limits for current models.
- The scarce resource is the **routing signal**, at two levels: the always-loaded **descriptions** (hard ~1%-of-context budget) and the within-skill **index** that points to the right file.
- **Levers:** keep descriptions lean + keyword-rich (survive the budget *and* route well); name skills descriptively (survive truncation); raise `skillListingBudgetFraction` to keep more resident (token cost); invest in index/description quality over structure.
- A smaller-context model gives a proportionally tighter skill budget — the main model-dependent factor (Haiku otherwise matched Opus on every test it was run on).
