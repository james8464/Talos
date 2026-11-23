PYTHON ?= python3

.PHONY: help test lint fix doctor graph clean

help:
	@echo "make test    run the fixture suite"
	@echo "make lint    check style and correctness"
	@echo "make fix     apply safe automatic fixes"
	@echo "make doctor  run the self-check (imports, syntax, lint, tests)"
	@echo "make graph   rebuild the local source graph"

test:
	$(PYTHON) -m unittest discover -s tests -p 'test_*.py'

lint:
	ruff check .

fix:
	ruff check . --fix

doctor:
	$(PYTHON) -m talos_core.cli doctor

graph:
	graphify extract . --code-only

clean:
	find . -name '__pycache__' -type d -prune -exec rm -rf {} +
	rm -rf .ruff_cache .pytest_cache
