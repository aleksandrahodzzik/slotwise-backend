.PHONY: up down test lint bench seed demo

up:
	docker compose up --build -d --wait

down:
	docker compose down -v

test:
	docker compose up -d db --wait
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
