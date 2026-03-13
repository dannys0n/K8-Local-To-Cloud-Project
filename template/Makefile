SHELL := /usr/bin/env bash

.PHONY: help tier1-help tier2-help tier3-help

help:
	@printf '%s\n' \
		'tier3-help Show the full base template commands' \
		'tier2-help Show the multi-node stripped template commands' \
		'tier1-help Show the minimal stripped template commands'

tier3-help:
	$(MAKE) -C tier3 help

tier2-help:
	$(MAKE) -C tier2 help

tier1-help:
	$(MAKE) -C tier1 help
