PYTHON ?= venv/bin/python
PORT ?= 8765
HTTP_PORT ?= 8000
DURATION ?= 120
SESSION ?= liverecall

.PHONY: help server client source

.DEFAULT_GOAL := help

help:
	@echo "Run each target in its own terminal, from the repo root:"
	@echo "  make server   WebSocket server on port $(PORT)"
	@echo "  make client   browser files at http://127.0.0.1:$(HTTP_PORT)"
	@echo "  make source   caption source for $(DURATION)s"
	@echo "Then open http://127.0.0.1:$(HTTP_PORT)"

server:
	$(PYTHON) solution_server.py --port $(PORT)

client:
	$(PYTHON) -m http.server $(HTTP_PORT) --directory client

source:
	$(PYTHON) caption_source.py --duration $(DURATION)