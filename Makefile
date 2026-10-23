ifeq ($(OS),Windows_NT)
SHELL := bash
endif

.PHONY: up down test lint bench seed demo

up:
	docker compose up --build -d --wait

down:
	docker compose down -v

test:
	docker compose up -d db --wait
	DATABASE_URL=postgresql+asyncpg://slotwise:slotwise@localhost:5432/slotwise \
	DB_POOL_SIZE=10 DB_MAX_OVERFLOW=60 BOOKING_STRATEGY=row_lock \
	uv run pytest --cov=slotwise --cov-report=term-missing

lint:
	uv run ruff check .
	uv run ruff format --check .
	uv run mypy src

bench:
	uv run python scripts/bench.py

seed:
	uv run python scripts/seed.py

demo: up seed
	uv run python -c "import webbrowser; webbrowser.open('http://localhost:8000')"
