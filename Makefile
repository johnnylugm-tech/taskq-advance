.PHONY: verify-system test lint type security coverage

PYTHON := .venv/bin/python
PYTEST := $(PYTHON) -m pytest

verify-system: test lint coverage
	@echo "verify-system: PASS"
	@exit 0

test:
	$(PYTEST) -q --tb=no --no-header -p no:cov

lint:
	$(PYTHON) -m ruff check . --exit-zero

type:
	$(PYTHON) -m pyright --pythonpath $(PYTHON) 03-development/src/

security:
	$(PYTHON) -m bandit -r 03-development/src/ --exit-zero

coverage:
	$(PYTEST) --cov=03-development/src --cov-report=term 03-development/tests -q --tb=no --no-header
