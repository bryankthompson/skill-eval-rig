# Activation test — does `/dir-reply` auto-activate on email/reply requests?

> **Scope:** this is the **command (`/dir-reply`) A/B** — the PR #2327 OLD-vs-REVISED description
> experiment. **Sibling:** `../RUNBOOK.md` covers **skill** auto-activation — the synthetic
> `vacuum-expert` / `ctx-policy-71` needle-`4200` budget/naming cliff probe.

The interactive, gold-standard follow-up to PR #2327 (mcp-local-directory) — the test the
headless `claude -p` probe **cannot** do (headless never auto-invokes a command; it only
forced-selects). This measures the thing that actually failed in the 2026-06-23 Tableau miss:
*does the model, on its own, reach for `/dir-reply` when the user asks to draft an email?*

## Why a fixture (not the real repo)
A fresh session **in mcp-local-directory is contaminated**: its SessionStart loads the recent
status-db sessions — including the one logging *"harden /dir-reply routing… email requests"* —
which primes the model to route email→/dir-reply. This fixture has no status-db / no CLAUDE.md
priming, so it isolates the routing-signal effect of the `description:` alone.

## A/B design
Two sibling fixtures, **identical except `/dir-reply`'s `description:`**:
- `dir-reply-old/`  — the pre-#2327 description (**zero** email/gmail/message tokens).
- `dir-reply/`      — the merged #2327 description (carries draft/write/email/message/reply/Gmail).

Both load the same 7 sibling stubs (dir-email-sync, dir-outreach, dir-plugin-outreach,
review-my-claims, dir-server-status, dir-fix-tests, dir-publish) **and** the same global
`~/.claude/commands/` set — so the globals (incl. the email-adjacent `/mcp-prime-dev-email`)
are a *constant* that cancels out in the A/B. Any OLD→REVISED routing delta is attributable to
the description change alone.

Each command is an inert **stub**: if the model invokes it, the stub replies `INVOKED /<name>`
and stops — so the routing choice is crisp in the transcript (no real side effects).

## How to run (VS Code → Run Task, or `cd <fixture> && claude "<prompt>"`)
Tasks are in `mcp-local-directory/.vscode/tasks.json`, labelled `dir-reply-activation [OLD|REVISED] …`.
Run each as a **fresh** session (the task clears the panel). For each prompt, record **which
command the model invoked** (look for `INVOKED /<name>`), or `none` if it answered in prose / went
straight for a raw tool.

## Battery + bar
**Positives** (where the description fix can move routing toward `/dir-reply` — but note OLD
*already* name-routes the "reply…" framings, so the measurable gain is on the non-"reply"
framings; see "Reading the result"):
- "create this draft email in gmail"
- "draft them an email"
- "write a reply to Joe about his submission"
- "reply to the partner on this thread"

**Near-miss negatives** (REVISED must NOT steal these):
- "sync my email tracking db with gmail"  → expect `/dir-email-sync`
- "this test is failing, fix it"           → expect `/dir-fix-tests`

## Reading the result
**Headline finding:** the `/dir-reply` command **name** is itself a strong router — under the
pre-#2327 (email-token-free) OLD description it *already* auto-fires `/dir-reply` on the "**reply**…"
framings, purely on the name⇄"reply" match. So the description fix's measurable effect is on the
framings the name can't reach (the non-"reply" email asks). A raw "≥3/4 gained" bar would mislabel
a working fix, because some positives are at ceiling under OLD with no headroom for the description
to move them.

So the verdict scores **marginal gain over the OLD-dark denominator** — the positives OLD did *not*
already route to `/dir-reply` — and a negative is **held** unless `/dir-reply` actually won it (a
dark answer, the owner command, or any other command all count as held; only `/dir-reply` stealing
a near-miss is a regression). This is what `score_battery` (in `drive_interactive.py`) computes; the
buckets it returns (match the doc to the code — the code is the source of truth):

- **`FIX VALIDATED`** — REVISED gains `/dir-reply` on **every** OLD-dark positive (full marginal
  gain) **and** holds the negatives.
- **`FIX EFFECTIVE (PARTIAL)`** — REVISED gains **some but not all** OLD-dark positives + holds negatives.
- **`NO HEADROOM (OLD already name-routes all positives)`** — OLD already fires `/dir-reply` on
  *all* positives via the name alone; the description delta is unmeasurable on this battery.
- **`DESCRIPTION-DELTA UNTESTABLE`** — `/mcp-prime-dev-email` wins at least `⌊n/2⌋` of the positives
  (floor — `max(1, n//2)` in the code; = half for the shipped 4-positive battery) under **both**
  arms, masking the delta (the global competitor still collides — a `/dir-reply` "verified partner
  reply" edge follow-up, tracked in status-db id 1441).
- **`FIX FAILED / INCONCLUSIVE`** — REVISED gains **none** of the OLD-dark positives, **or** steals
  a negative into `/dir-reply` (a stolen negative always overrides partial gain — `FIX EFFECTIVE
  (PARTIAL)` requires the negatives held, so partial-gain-with-a-steal falls through to here).

- **n is small + interactive** — run each prompt 2–3× if a result looks flaky; auto-activation is
  the documented silent-cliff axis (`reference_skill_eval_rig`), so a single trial is indicative,
  not definitive.
