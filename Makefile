# skill-eval-rig — developer tasks.
# `make test` runs the scorer self-tests against committed fixtures (no live `claude`, CI-safe).

.PHONY: test
test:
	python3 tests/test_score.py
