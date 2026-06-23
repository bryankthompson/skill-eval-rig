# Findings — Claude Agent Skills behavior under stress

Empirical map from driving the `claude` CLI (2.1.185) against synthetic skill fixtures, **Opus 4.8 (1M ctx)** and **Haiku 4.5**, ~250 trials total. Single-needle retrieval unless noted; N=4–5/cell. These characterize *mechanisms*, not a reliability guarantee — replicate on your own skills before quoting rates. Every number here was re-scored from raw transcripts (preserved privately — see `results/README.md`); verdicts that hinge on token-presence were hand-verified (the `--attack` scorer is robust to the substring over-count but a negation-adjacent compromise is a known hand-verify edge — see `score.py` / `tests/test_score.py`).

## The one-line throughline
**The number/size/nesting/depth of reference files is *not* the binding constraint.** The scarce resources are (1) the **routing signal** (the always-loaded descriptions + the index/filenames) and (2) the **always-loaded listing budget**. And there is a sharp **model asymmetry**: the weaker model fails *confidently* on adversarial/misleading skill content exactly where the stronger model detects and refuses.

## What does NOT break (both models, robust)
- **Selection scale** — one skill, 100 → **1000** reference files: routes to the right file (~1 read) *when the index discriminates*.
- **Chaining depth** — 2 → **10 hops**, incl. an oversized mid-chain file: full reads, no degradation. The spec's `head -100` partial-read concern did not reproduce.
- **Nav by filename meaning** — `ls` 300 files, pick by semantic filename match with no grep handle.
- **2-level hubs** (SKILL.md→domain index→leaf) — Opus clean; Haiku ~½ brute-forces but still lands.
- **Cross-skill handoff** (interactive, auto-activation) — entry skill fires → reaches skill-B, for a *file pointer* AND for *logic in skill-B's body*. → "disjointed skills referencing each other" works; gaps are activation gaps.
- **Tied ambiguity** — both surface the conflict rather than silently picking.
- **Context-retention / scale** — could not induce exhaustion: the model **reads efficiently** (shortcuts recognized filler chains, infers patterned sums, greps) and dodges forced accumulation. Recall of an early key held across long chains because the bulk was never actually loaded.

## Where the line actually is
- **Routing-signal quality** — identical 500-file corpus + query; flip the needle's index line from generic→discriminating: **0/5 → 5/5** on both models. Collapse needs *both* channels dead: an uninformative index AND a query lexically disjoint from the file body (a uniform index alone recovers via grep). Meaning-routing lives in the description/index; the body-search fallback is *lexical*.
- **Listing budget** — `skillListingBudgetFraction`, **default 1% of context, char-denominated** (~30K chars on a 1M-ctx model; ~6× tighter on 200K). Over budget, descriptions are dropped **most-expensive-first** (the fattest descriptions are evicted first; lean ones resist) and **skill names are always kept** (documented: code.claude.com/docs/en/skills). The size/cost-based order is what we observed via `/doctor` and is consistent across the generator knobs (`--needle-desc-chars` / `--filler-desc-chars` only make sense under size-based eviction); an earlier draft said "least-invoked-first" — that was wrong.
- **Activation cliff** (interactive only — `-p` resolves skills only via explicit `/name`) — a skill goes dark for auto-discovery, failing *silently* (confident wrong answer, no error), **only** when its description is dropped **and** its name is uninformative. A descriptive name survives the drop. Replicated n=3.

## The model asymmetry (the headline)
| Adversarial regime | Opus 4.8 | Haiku 4.5 |
|---|---|---|
| Misleading description, *plausible* decoy content | refuses (detects inconsistency) | **confident-wrong** (returns decoy) |
| Misleading description, content obviously off-topic | refuses | refuses (catches it) |
| Injection — blatant ("SYSTEM OVERRIDE") | resists + flags | resists |
| Injection — *plausible* ("2026 policy note" / "CVE, value deprecated") | resists + flags | **COMPROMISED** (adopts attacker value) |
| Multi-file synthesis (sum K) | clean through K=50 | mis-sums from ~K=8 (arithmetic, not recall) |

**The more *legitimate* an embedded instruction looks, the more it slips past the weaker model; the stronger model flags all framings.** See `SECURITY.md`.

## Instruction precedence (skill vs CLAUDE.md vs user)
- **CLAUDE.md policy/prohibition OVERRIDES a skill** (both models refused to output a value the skill said to report) — you *can* govern skills with project policy.
- **CLAUDE.md formatting composes over skill output** (both wrapped the skill's value per the rule).
- **No reliable hierarchy for a factual VALUE conflict** — the freshly-read skill reference often wins (Opus 3/3 took the skill's value over CLAUDE.md's). Don't rely on CLAUDE.md to *correct* a skill's facts.
- A skill's standing "ALWAYS do X" can override an explicit user request (Opus honored "always hex" over the user's "decimal," while flagging it).

## Practical implications
- Invest in **descriptions/index/filename quality** (the routing signal) — not in count/nesting/depth.
- Keep descriptions **lean and keyword-rich** — survives the budget *and* the budget's drop order.
- **Name skills descriptively** — a descriptive name keeps a skill discoverable even when its description is truncated.
- **Sanitize reference content** you don't control — it's a prompt-injection surface, serious on weaker models.
- Use **CLAUDE.md policy** to constrain skills; don't expect CLAUDE.md *data* to override a skill's facts.
- Match model to risk: weaker models are measurably less safe against misleading/adversarial skill content.

## Honest limits
Single-needle retrieval; N=4–5/cell; synthetic fixtures tuned to isolate mechanisms; the cross-skill/activation results are interactive and lower-N; "could not break Opus" ≠ "unbreakable" (we did not reach context-exhaustion or multi-turn manipulation).

**Which rows the committed harness reproduces vs. exploratory.** `experiments/{selection,chaining,budget,adversarial,synthesis,precedence}.sh` reproduce the selection (incl. the 500-file disjoint-body 0/5↔5/5 contrast), chaining, budget, injection (5 framings), synthesis, and precedence rows; the activation / cross-skill rows are reproduced by the interactive `experiments/activation/` runbook + `prefill_report.py` (raw transcripts preserved privately). The **2-level hubs**, **tied ambiguity**, **context-retention / scale-dodge**, and **nav-by-semantic-filename** rows were *exploratory probes not yet in the committed harness* — treat them as directional until a committed script builds them.

**Uncertainty.** Rates are point estimates at N=4–5/cell (n=3 for the adversarial/precedence cells), no fixed seed — reproductions vary; read them as mechanism characterizations, not precise rates.

**Scope note.** The selection/chaining axes *force* the skill via `/skill` (retrieval); activation measures *auto-discovery*. The "routing signal is the constraint" throughline spans both, but they are different regimes — don't read a forced-retrieval rate as an activation rate.
