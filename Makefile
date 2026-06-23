# skill-eval-rig — developer tasks.
# `make test` runs the scorer + prefill self-tests against committed fixtures (no live `claude`,
# CI-safe). These pin the instrument behind every published number.

.PHONY: test
test:
	python3 -m unittest discover -s tests -p 'test_*.py' -v
