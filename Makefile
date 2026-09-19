.PHONY: help install up down dev migrate revision seed worker beat lint fmt typecheck test check

help:
	@grep -E '^[a-z-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

install:  ## Install dependencies
	uv sync

up:  ## Start postgres, redis, mailhog
	docker compose up -d

down:  ## Stop infrastructure
	docker compose down

dev:  ## Run the API with reload
	uv run uvicorn app.main:app --reload --port 8000

migrate:  ## Apply migrations
	uv run alembic upgrade head

revision:  ## Autogenerate a migration: make revision m="add users"
	uv run alembic revision --autogenerate -m "$(m)"

worker:  ## Run the celery worker
	uv run celery -A app.workers.celery_app worker -l info

beat:  ## Run celery beat
	uv run celery -A app.workers.celery_app beat -l info

seed:  ## Load a demo shop with a month of history
	uv run python -m scripts.seed

lint:  ## Lint
	uv run ruff check app tests scripts

fmt:  ## Format + autofix
	uv run ruff format app tests scripts && uv run ruff check --fix app tests scripts

typecheck:  ## Type check
	uv run mypy app scripts

test:  ## Run tests
	uv run pytest

check: lint typecheck test  ## Everything CI runs
