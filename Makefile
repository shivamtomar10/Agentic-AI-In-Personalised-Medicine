# Convenience wrapper around run.py (Windows users can skip make and run `python run.py <task>` directly).
PYTHON ?= python
.PHONY: setup test eval eval-quick cohort verify serve
setup:      ; $(PYTHON) run.py setup
test:       ; $(PYTHON) run.py test
eval:       ; $(PYTHON) run.py eval
eval-quick: ; $(PYTHON) run.py eval-quick
cohort:     ; $(PYTHON) run.py cohort
verify:     ; $(PYTHON) run.py verify
serve:      ; $(PYTHON) run.py serve
