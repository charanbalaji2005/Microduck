"""python -m microduck_x — run the control service (localhost only unless told otherwise)."""
from __future__ import annotations

import sys

import uvicorn

from .app import create_app
from .config import Settings
from .events import setup_logging


def main() -> None:
    settings = Settings()
    problems = settings.validate()
    if problems:
        for p in problems:
            print(f"refusing to start: {p}", file=sys.stderr)
        sys.exit(2)
    setup_logging()
    uvicorn.run(create_app(settings), host=settings.host, port=settings.port, log_level="warning",
                ws_ping_interval=10, ws_ping_timeout=20)


if __name__ == "__main__":
    main()
