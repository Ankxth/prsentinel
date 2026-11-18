# backend

FastAPI service served with uvicorn.

## Run it

```bash
cd backend
python -m venv .venv
source .venv/bin/activate          # Windows Git Bash: source .venv/Scripts/activate
pip install -r requirements.txt
cp .env.example .env               # then fill in what you need
uvicorn app.main:app --reload      # http://127.0.0.1:8000/health
python -m pytest -q
```

On Render the start command is
`uvicorn app.main:app --host 0.0.0.0 --port $PORT`.

## Layout

```
app/
  main.py        create_app(): builds the FastAPI app
  core/          settings (config.py); later auth, logging, rate limits
  api/           one router file per area (health.py so far)
  db/            (later) models, session, Alembic migrations
  services/      (later) context engine, LLM router, classifier, runs
  agents/        (later) vulnerability proof agent
tests/
```

See `../docs/context.md` for how the pieces fit together.
