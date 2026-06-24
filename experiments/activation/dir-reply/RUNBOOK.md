# Activation test — does `/dir-reply` auto-activate on email/reply requests?

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
**Positives** (the bar: OLD *misses* dir-reply, REVISED *fires* it):
- "create this draft email in gmail"
- "draft them an email"
- "write a reply to Joe about his submission"
- "reply to the partner on this thread"

**Near-miss negatives** (REVISED must NOT steal these):
- "sync my email tracking db with gmail"  → expect `/dir-email-sync`
- "this test is failing, fix it"           → expect `/dir-fix-tests`

## Reading the result
- **Fix validated** ⇒ REVISED fires `/dir-reply` on ≥3/4 positives where OLD did not, AND REVISED
  holds the negatives on their owners.
- **Watch for** `/mcp-prime-dev-email` winning the email positives even under REVISED — if so, the
  two descriptions still collide and `/dir-reply` may need a sharper "verified partner reply" edge
  (a follow-up, tracked in status-db id 1441).
- **n is small + interactive** — run each prompt 2–3× if a result looks flaky; auto-activation is
  the documented silent-cliff axis (`reference_skill_eval_rig`), so a single trial is indicative,
  not definitive.
