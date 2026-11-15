"""Real storage: SQLite (app.db), append-only events.jsonl and the markdown wiki.

`RealStore` offers the same public methods and response shapes as
`app.mock_store.MockStore`, so routes can swap it in when MOCK_MODE=0.
"""

from .real_store import RealStore

__all__ = ["RealStore"]
