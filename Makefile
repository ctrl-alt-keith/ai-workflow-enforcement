.PHONY: help check install verify-install uninstall

INSTALL_BIN ?= $(HOME)/.local/bin
INSTALL_FORCE_ARG := $(if $(filter 1 true yes,$(FORCE)),--force,)
INSTALL_DIRTY_ARG := $(if $(filter 1 true yes,$(ALLOW_DIRTY)),--allow-dirty,)

.DEFAULT_GOAL := check

help: ## List available repo-local Makefile targets with short descriptions.
	@awk 'BEGIN {FS = ":.*## "}; /^[a-zA-Z0-9_.-]+:.*## / {printf "  %-24s %s\n", $$1, $$2}' $(MAKEFILE_LIST)

check: ## Run focused Python unit tests.
	python3 -m unittest discover -s tests

install: ## Install the reviewed codex-safe-rm control.
	python3 -m enforcement.install_safe_rm install --destination "$(INSTALL_BIN)/codex-safe-rm" $(INSTALL_FORCE_ARG) $(INSTALL_DIRTY_ARG)

verify-install: ## Verify the installed codex-safe-rm control and provenance.
	python3 -m enforcement.install_safe_rm verify --destination "$(INSTALL_BIN)/codex-safe-rm"

uninstall: ## Remove an owned codex-safe-rm installation.
	python3 -m enforcement.install_safe_rm uninstall --destination "$(INSTALL_BIN)/codex-safe-rm" $(INSTALL_FORCE_ARG)
