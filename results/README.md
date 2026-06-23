# results/

Trial outputs (`*.jsonl` stream-json + scored summaries) land here and are **gitignored** —
never commit a clone's real-corpus transcripts (they can carry session content).

**Verifying the scoring.** The committed `tests/` fixtures demonstrate the scoring pipeline
end-to-end; `make test` re-scores them and pins every verdict. To regenerate the empirical
results, run the `experiments/*.sh` scripts — each prints its scored summary to stdout.

**Raw campaign transcripts** from the original runs are preserved privately (not committed) and
available on request; the published rates were re-scored from them.
