.PHONY: setup seed dev run

setup:
	python3 -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt

seed:
	. .venv/bin/activate && python -m scripts.seed

dev:
	. .venv/bin/activate && uvicorn app.main:app --reload --port 8000

run: dev
