# Memory-recall mechanism — empirical characterization

**Question:** how does Claude Code's built-in **"auto-memory"** actually inject memory into a
session's context? Characterized from the OUTSIDE (no source access) by planting controlled probes
in a sandbox project's memory dir and observing what reaches a driven session.

**Pinned to:** `claude 2.1.198`; probe model `claude-haiku-4-5-20251001` (headless) + the session
model (interactive pty). Detection surface: **model self-report** in the `-p` / session output
(the only admissible surface — see Phase 0). Fixtures/harness: `gen_memory.py`,
`experiments/memory-recall.sh`, `experiments/memory-recall/mem_drive.py`. Re-run Phase 0:
`bash experiments/memory-recall.sh --smoke`.

## Headline (report loudly)

**A commonly-assumed recall claim — that topic files are recall-injected into `<system-reminder>`
blocks when their `description:` matches the task — does NOT reproduce.** Across every arm (sandbox
headless N=3, sandbox interactive N=1, and a sweep of 20 recent *live* fully-indexed sessions),
**no topic file was ever auto-recalled by description match.** The ONLY automatic push is
**`MEMORY.md` always-load** (injected whole, as a `<system-reminder>`, at session start). Topic
files reach context **only via explicit model reads** ("loaded on demand", model-driven) — matching
the *public* docs, contradicting the description-match assumption.

**Security-surface implication (general):** the *automatic* context-injection surface is **narrow**.
There is no description-triggered topic-file injection. The automatic surface is (1) `MEMORY.md`'s
**full body** (always-load) and (2) any file the model is *induced to Read*. A stale/adversarial
topic-file **body** is NOT auto-injected — it can influence a session only if the model is
separately induced to read that file. Any audit of the memory-injection surface should target
MEMORY.md content + read-inducement, not description-match recall.

## Detection-surface admissibility (Phase 0 — the gate)

A positive-control sentinel `MEMCTRLAAAA` was planted in the always-load `MEMORY.md`. A surface is
ADMISSIBLE only if it shows the positive control; a no-fire from a blind surface is discarded.

| Surface | Positive control visible? | Verdict |
|---|---|---|
| (a) model self-report in output | **YES** — model reported `MEMCTRLAAAA` and attributed it to "the always-load memory index (`MEMORY.md`), injected as `<system-reminder>` context" | **ADMISSIBLE** (sole surface used) |
| (b) transcript jsonl grep | NO — `MEMCTRLAAAA` appears only in `type=assistant` lines (the model's own echo), never in an injected/system block; the injected system prompt is not serialized per-turn | **BLIND** |
| (c) `--debug-file` | NO — only skill-listing-budget + settings-symlink markers; zero memory/recall markers | **BLIND (dropped)** |

This confirms both plan-gate Criticals: injection lands in the **system prompt**, which Claude
Code does not serialize to the transcript, so self-report is the only way to observe it.

## The 4 questions

### Q1 — Does description-recall injection fire at all? → **NO (only MEMORY.md always-loads)**

Pre-registered outcomes: *interactive-only* (fails headless, passes pty) · *doesn't-fire* (positive
control passes, probe dark in ALL admissible surfaces across headless+pty) · *fires-but-missed*
(ruled out iff a same-surface positive control passed).

| Cell | Positive control | Probe `MEMDESCQWRT` (desc) | Probe `MEMBODYZXCV` (body) | Verdict |
|---|---|---|---|---|
| headless, desc-matched prompt ×3 | PASS ×3 | dark ×3 | dark ×3 | no-fire (admissible) |
| headless, non-matching prompt | PASS | dark | dark | no-fire (expected) |
| headless, MEMORY.md **links** the probe | PASS | dark | dark | no auto-inject (a link was PRESENT but not read — see caveat) |
| interactive pty, desc-matched prompt ×1 | PASS | dark | dark | no-fire (admissible) |
| **live store, 20 recent sessions** (ad-hoc sweep) | n/a | **0 topic-file auto-injections** (8 arrived via explicit reads) | — | no-fire |

> **q1c caveat (what the linked cell does and does NOT show):** it only makes a `[link](probe.md)`
> *present* in the always-load `MEMORY.md`; it does not *induce* a read, and the link text omits
> the nonces. A dark q1c therefore means "the model did not spontaneously read the linked file" —
> it supports *non-injection* (the link's mere presence injects nothing) but does **not** positively
> demonstrate that an on-demand read *path* works (there is no read-path positive control in the
> committed battery). The "reach context via explicit reads" half rests on the ad-hoc live-sweep's
> 8/20 read-arrivals, not a committed read-inducement cell.

**Outcome: DOESN'T-FIRE-AT-ALL.** Positive control passed in headless AND interactive, and the
probe never surfaced on the admissible surface in any cell → not interactive-only, not
fires-but-missed. Corroborated by the live-store sweep (a fully-indexed store also never
auto-injects a topic file).

**Embedding/qdrant confound — RESOLVED.** Candidate objection: "a freshly-dropped file isn't
embedded, so recall can't find it." Ruled out three ways: (1) **no auto-indexer exists** — the only
memory hook is a write-time *gate* (reads/writes nothing); no
launchd/cron memory→qdrant sync — so a *description*-based recall would scan the planted file
regardless of embeddings; (2) the **fully-indexed live store** (983 files, embedded over months)
auto-recalled a topic file in **0/20** recent sessions — an **ad-hoc read-only analysis** (not a
committed harness cell) counting, across the 20 most-recent live transcripts, how many carried a
topic-file frontmatter (`node_type: memory`) via a `<system-reminder>` injection vs an explicit
Read/tool_result → 0 injected, 8 via reads; (3) the **empty-dir control** (the committed
`control-empty` cell — a bare sandbox with NO memory planted) invents no sentinel, AND in every
probe cell the detector reports ONLY the always-load `MEMCTRLAAAA` while the probe nonces stay dark
— so it is not blindly echoing every `MEM…` token from the prompt. (A `--bare` auto-memory-OFF arm
was NOT run / is not in the committed battery; the empty-dir + selective-echo controls are the
code-backed echo-ruling-out.)

### Q2 — Match algorithm (keyword / semantic / ranked)? → **MOOT / N/A**

There is no description-based topic recall, so there is no match algorithm to characterize. The
sole automatic push (`MEMORY.md`) is **unconditional always-load**, not a query-vs-description
match. (The `gen_memory.py --match lexical|semantic|none` machinery + the zero-shared-token
disjointness assertion are built and ready to re-test if a future CLI version turns recall on.)

### Q3 — Payload: description-only, full body, or both? → **full body (of MEMORY.md); topic bodies not auto-injected**

The body-nonce was made **behaviorally load-bearing** (an instruction — "emit `MEMBODYZXCV` and
answer in French" — that only a model which *read the body* could satisfy), so self-report reflects
body *ingestion*, not mere echo.

- For the ONLY auto-injected file (`MEMORY.md`): the model quoted its **full body** (the whole
  "Positive control" section text), i.e. always-load injects the **entire file content**, not a
  description summary. So a stale/adversarial `MEMORY.md` body IS a live injection surface.
- For topic files: not auto-injected at all → the desc-vs-body question is moot for them. The
  behavioral body instruction was never triggered (French answer never occurred), consistent with
  the body never entering context.

### Q4 — When does it fire? → **session-start always-load; no per-prompt/mid-session topic recall**

`MEMORY.md` is present from the **first turn** (headless single-shot and interactive turn 1), and
the model attributes it to a session-start `<system-reminder>` injection. No topic-file recall
fires at session-start, per-prompt, or mid-session (Q1). (The Q4 multi-turn + fresh-session-Y
timing battery is implemented in `mem_drive.py --per-turn` but is moot given no topic recall to
time; re-runnable if recall is later enabled.)

## Confidence & limits

- **Strong:** the MEMORY.md-always-load-only result is positive-control-gated, reproduced N=3
  headless + N=1 interactive + corroborated by 20 live sessions + a no-auto-indexer structural
  check. The self-referential-echo trap (a naive grep scoring the *prompt/docstring* echoed back)
  was avoided by isolating the sandbox and never reading the probe files in-session.
- **Limits (be honest about what is code-backed vs ad-hoc):**
  - interactive pty N=1 (paid + TUI-flaky; corroborated by the headless N=3 + the live sweep, not
    independently repeated). Single sandbox topic per arm.
  - The **negative side is code-backed** (committed cells: positive control + empty-dir control +
    probe-dark). The **positive "reach-via-reads" side is NOT** — it rests on the *ad-hoc* 20-session
    sweep (8/20 via reads), which is not a committed harness cell; and no committed cell positively
    demonstrates a read-*induced* sentinel lighting up the detector (no read-path positive control).
  - **`--bare` (auto-memory OFF) was not run** — the echo-ruling-out is the empty-dir + selective-echo
    controls, not a bare-mode delta.
  - Pinned to `claude 2.1.198` — a future CLI could enable description recall, at which point the
    `--match`/behavioral-body machinery re-tests it directly.
  - Not tested: whether an in-session `qdrant_store` + same-session `qdrant_find` (model-invoked, not
    "auto") behaves differently — that is a *tool*, not the auto-memory feature under test.

## Reusable harness

- `gen_memory.py` — plant a probe memory dir; hard safety guard (temp-root + no-clobber marker)
  BEFORE any write; `--match` disjointness assertion; behavioral body-nonce.
- `experiments/memory-recall.sh` — headless battery (Phase 0 + Q1 + controls + linked variant),
  slug↔transcript attribution assert (a plumbing miss → `INVALID`, never a no-fire), cleanup trap.
- `experiments/memory-recall/mem_drive.py` — interactive pty arm (submit-retry; multi-turn for Q4).
- Tests: `tests/test_gen_memory.py` (slug rule + safety guards + disjointness assertion, offline).
