"""ASGI entry-point alias.

The canonical app is ``app.main:app``. Some deploy configs reference
``app.api:app`` instead, so this module re-exports the same ASGI application to
make either start command work:

    uvicorn app.main:app   # canonical
    uvicorn app.api:app    # alias (this module)
"""
from app.main import app  # noqa: F401  (re-exported for uvicorn)
