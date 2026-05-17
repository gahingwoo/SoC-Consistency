# SoC-Consistency — common development tasks
#
# Usage:
#   make install      install the package in editable mode
#   make test         run the full test suite
#   make check        run socc on the bundled mainline DTS example
#   make rules        list all registered rules for rk3588
#   make clean        remove build artefacts and __pycache__ trees

.PHONY: all install test check rules lint clean generate-dts

PYTHON  := python3
VENV    := .venv
PIP     := $(VENV)/bin/pip
PYTEST  := $(VENV)/bin/python -m pytest
SOCC    := $(VENV)/bin/socc

EXAMPLE_DTS := data/examples/rk3588s-orangepi-5.dts

all: install

# ---------------------------------------------------------------------------
# Environment
# ---------------------------------------------------------------------------

$(VENV)/bin/activate:
	$(PYTHON) -m venv $(VENV)

install: $(VENV)/bin/activate
	$(PIP) install -e ".[dev]" --quiet

# ---------------------------------------------------------------------------
# Testing
# ---------------------------------------------------------------------------

test: install
	$(PYTEST) tests/ -v

# ---------------------------------------------------------------------------
# Demo run against the mainline kernel DTS fixture
# ---------------------------------------------------------------------------

check: install
	$(SOCC) check $(EXAMPLE_DTS) --soc rk3588

# ---------------------------------------------------------------------------
# Rules
# ---------------------------------------------------------------------------

rules: install
	$(SOCC) rules --soc rk3588

# ---------------------------------------------------------------------------
# Linting
# ---------------------------------------------------------------------------

lint: install
	$(VENV)/bin/python -m flake8 socc/ tests/ --max-line-length=100 --extend-ignore=E203,W503

# ---------------------------------------------------------------------------
# Regenerate the mainline DTS fixture from the Linux kernel source
#
# Requires: curl, clang, dtc
# ---------------------------------------------------------------------------

generate-dts:
	@echo "Downloading mainline RK3588s OrangePi 5 DTS from torvalds/linux..."
	@bash scripts/generate_dts_fixture.sh

# ---------------------------------------------------------------------------
# Cleanup
# ---------------------------------------------------------------------------

clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name "*.egg-info" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name ".pytest_cache" -exec rm -rf {} + 2>/dev/null || true
	find . -name "*.pyc" -delete 2>/dev/null || true
	@echo "Clean."
