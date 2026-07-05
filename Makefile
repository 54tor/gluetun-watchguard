IMAGE ?= sat0r/gluetun-watchguard
TAG ?= dev

.PHONY: install test lint run build

install:
	pip install -e ".[dev]"

test:
	pytest -q

lint:
	ruff check .

run:
	python -m gluetun_watchguard

build:
	docker build -t $(IMAGE):$(TAG) .
