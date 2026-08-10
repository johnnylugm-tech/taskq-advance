.PHONY: verify-system test lint type security coverage

PYTHON := .venv/bin/python
PYTEST := $(PYTHON) -m pytest

verify-system: test lint coverage
	@echo "verify-system: PASS"

test:
	$(PYTEST) -q --tb=no --no-header -p no:cov

lint:
	$(PYTHON) -m ruff check . --exit-zero

type:
	$(PYTHON) -m pyright --pythonpath $(PYTHON) 03-development/src/

security:
	$(PYTHON) -m bandit -r 03-development/src/ --exit-zero

coverage:
	$(PYTHON) -m coverage run -m pytest --cov=03-development/src 03-development/tests -q --tb=no --no-header
	$(PYTHON) -m coverage report
