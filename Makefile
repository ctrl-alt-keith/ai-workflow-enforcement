.PHONY: help check workflow-drift-setup workflow-drift-audit install verify-install uninstall

INSTALL_BIN ?= $(HOME)/.local/bin
INSTALL_FORCE_ARG := $(if $(filter 1 true yes,$(FORCE)),--force,)
INSTALL_DIRTY_ARG := $(if $(filter 1 true yes,$(ALLOW_DIRTY)),--allow-dirty,)
REPOSITORY_ROOT := $(abspath $(dir $(shell git rev-parse --path-format=absolute --git-common-dir)))
WORKFLOW_DRIFT_WORKSPACE ?= $(abspath $(REPOSITORY_ROOT)/..)

.DEFAULT_GOAL := check

help: ## List available repo-local Makefile targets with short descriptions.
	@awk 'BEGIN {FS = ":.*## "}; /^[a-zA-Z0-9_.-]+:.*## / {printf "  %-24s %s\n", $$1, $$2}' $(MAKEFILE_LIST)

check: ## Run focused Python unit tests.
	python3 -m unittest discover -s tests

workflow-drift-setup: ## Verify the dependency contract for the hosted workflow drift audit.
	@python3 -c 'import sys; assert sys.version_info >= (3, 12), "Python 3.12 or newer is required"'
	@gh --version >/dev/null

workflow-drift-audit: ## Run the canonical advisory workflow drift scan.
	@python3 -m enforcement.cli \
		--config config/workflow-drift-audit.json \
		--notes-root "$(WORKFLOW_DRIFT_WORKSPACE)/ai-workflow-incubator" \
		--playbook-root "$(WORKFLOW_DRIFT_WORKSPACE)/ai-workflow-playbook/docs" \
		--workspace-root "$(WORKFLOW_DRIFT_WORKSPACE)" \
		--output-format json

install: ## Install the reviewed codex-safe-rm control.
	python3 -m enforcement.install_safe_rm install --destination "$(INSTALL_BIN)/codex-safe-rm" $(INSTALL_FORCE_ARG) $(INSTALL_DIRTY_ARG)

verify-install: ## Verify the installed codex-safe-rm control and provenance.
	python3 -m enforcement.install_safe_rm verify --destination "$(INSTALL_BIN)/codex-safe-rm"

uninstall: ## Remove an owned codex-safe-rm installation.
	python3 -m enforcement.install_safe_rm uninstall --destination "$(INSTALL_BIN)/codex-safe-rm" $(INSTALL_FORCE_ARG)
