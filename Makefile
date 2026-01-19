# iac-driver Makefile

.PHONY: help install-deps install-dev test lint

help:
	@echo "iac-driver - Infrastructure orchestration engine"
	@echo ""
	@echo "  make install-deps  - Install required system packages"
	@echo "  make install-dev   - Install development dependencies (pre-commit, linters)"
	@echo "  make test          - Run unit tests"
	@echo "  make lint          - Run pre-commit hooks (pylint, mypy)"
	@echo ""
	@echo "Secrets Management:"
	@echo "  Secrets are managed in the site-config repository."
	@echo "  See: ../site-config/ or https://github.com/homestak-dev/site-config"
	@echo ""
	@echo "  cd ../site-config && make decrypt"

install-deps:
	@echo "Installing iac-driver dependencies..."
	@apt-get update -qq
	@apt-get install -y -qq python3 python3-yaml > /dev/null
	@echo "Done."

install-dev:
	@echo "Installing development dependencies..."
	pip install pre-commit pylint mypy types-PyYAML types-requests pytest
	pre-commit install
	@echo "Done. Pre-commit hooks installed."

test:
	@echo "Running unit tests..."
	python3 -m pytest tests/ -v

lint:
	@echo "Running pre-commit hooks..."
	pre-commit run --all-files
