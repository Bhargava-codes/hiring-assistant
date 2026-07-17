.PHONY: setup seed dev run evals evals-live

setup:
	python3 -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt

seed:
	. .venv/bin/activate && python -m scripts.seed

dev:
	. .venv/bin/activate && uvicorn app.main:app --reload --port 8000

run: dev

# Stage-by-stage evals (offline = deterministic + free; live = configured model)
evals:
	. .venv/bin/activate && python -m evals.run_evals

evals-live:
	. .venv/bin/activate && python -m evals.run_evals --live

# Behavioural evals: multi-turn scenario/goal + instruction adherence (LLM-judge)
evals-scenarios:
	. .venv/bin/activate && python -m evals.scenarios

evals-goalpush:
	. .venv/bin/activate && python -m evals.goal_push

evals-scenarios-live:
	. .venv/bin/activate && python -m evals.scenarios --live
