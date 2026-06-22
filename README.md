# skill-eval-rig

A small harness for **empirically validating Claude Agent Skills behavior** by driving the
`claude` CLI against synthetic skill fixtures and scoring what actually happens — instead of
reasoning from the spec. Built to answer real questions about structuring skills at scale
(how many, how big, how deep, how many reference files, what breaks).

It exercises four axes:

| Axis | What it tests | Mode | Headline finding (our runs) |
|---|---|---|---|
| **selection** | find the 1 right file among N | headless | scales to 1000 files **iff routing signal is good**; uniform index + non-lexical query → **collapses (0/5)** |
| **chaining** | reach a file *through* other files | headless | **never broke** to 10 hops + oversized files (the spec's `head -100` partial-read didn't reproduce) |
| **budget** | the always-loaded listing limit | headless (structural) | `skillListingBudgetFraction`, default **1% of context, char-denominated** (~30K chars on a 1M-context model) |
| **activation** | does a skill *auto*-fire, and survive truncation | **interactive only** | dies *silently* only when description dropped **and** name uninformative |

## The one thing to know first: headless vs interactive

- `claude -p` (headless) can drive **selection, chaining, and budget** — and it *can* produce real failures (the selection collapse is headless).
- `claude -p` **cannot test auto-activation** — in `-p` mode skills resolve only via explicit `/skill-name`, so the model never decides on its own to invoke one. The activation axis therefore needs **interactive** sessions (`experiments/activation/`, driven by a VS Code `tasks.json` + a human-in-the-loop runbook).
- **Implication for your own CI:** automated eval will silently miss the activation cliff. Budget pressure that drops a skill's description can make it stop auto-firing, and headless tests won't catch it.

## Quick start
```
# structural budget probe (no inference cost)
bash experiments/budget.sh

# selection: good vs uniform index at 100 and 1000 files, opus + haiku
bash experiments/selection.sh

# chaining: depth 2/5/10 (+ oversized mid-chain file), opus + haiku
bash experiments/chaining.sh

# activation: interactive — see experiments/activation/RUNBOOK.md
```
Pass a single model to the headless scripts to halve the runs, e.g. `experiments/selection.sh claude-opus-4-8`.

## Building your own conditions
- `gen_skill.py` — one skill with reference files. `--mode selection --files N --index good|uniform|nav`, or `--mode chain --chain-depth D [--big-step K]`. `--needle TOKEN`, `--leaf-lines L`.
- `gen_listing.py` — a listing of N filler skills + a needle skill, for budget/activation. `--needle-name` (descriptive vs opaque), `--needle-desc-chars`, `--fillers`, `--filler-desc-chars`, `--budget-fraction`.
- `run_trials.sh <proj> <skill|-> <model|-> <N> <question> [outdir]` — N headless trials → stream-json.
- `score.py --dir <out> --needle TOKEN [--right-file NAME]` — correct%, right-file, files-read, partial-reads, nav strategy.

**Point it at your real skills:** drop your skill folders into a project's `.claude/skills/`, then use `run_trials.sh` + `score.py` with your own questions/needles. The generators are only for controlled synthetic conditions.

## Auth note
The scripts `env -u ANTHROPIC_API_KEY claude …` so the CLI uses your interactive OAuth
credentials (a stale/invalid `ANTHROPIC_API_KEY` in the environment otherwise shadows them
with an "Invalid API key" error). On a clean machine with a valid key, drop the `env -u`.

## Limits (read before quoting results)
- Single-needle retrieval, not multi-document synthesis or ambiguous real queries.
- Small N (4–5/cell in our runs) — bump it for tighter rates.
- Synthetic fixtures; the generators make the regimes *clean* (e.g. the uniform-index break is
  deliberately lexically disjoint). They show the **mechanisms**, not your corpus's real
  ambiguity. Replicate on a slice of your actual skills before drawing hard conclusions.
- Findings are tied to the model versions and `claude` CLI version you run (we used
  `claude 2.1.185`, Opus 4.8 / Haiku 4.5). See `RESULTS.md` for the captured baseline.
