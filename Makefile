.PHONY: setup db seed dev test lint

setup:
	python -m venv .venv
	.venv/bin/pip install -e ".[dev,groq]"

db:
	docker compose up -d db
	.venv/bin/python scripts/init_db.py

seed:
	.venv/bin/python scripts/seed.py

dev:
	.venv/bin/python -m uvicorn app.main:app --reload

test:
	.venv/bin/python -m pytest tests/ -q

lint:
	.venv/bin/ruff check app tests
	.venv/bin/ruff format --check app tests
