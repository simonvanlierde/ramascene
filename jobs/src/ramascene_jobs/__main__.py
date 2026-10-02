"""Entrypoint: `python -m ramascene_jobs` (the image's CMD) or `ramascene-jobs`."""

import uvicorn

from ramascene_jobs.app import create_app
from ramascene_jobs.config import Settings


def main() -> None:
    """Serve until stopped, on HOST:PORT from the environment."""
    settings = Settings.from_env()
    uvicorn.run(create_app(settings), host=settings.host, port=settings.port)


if __name__ == "__main__":
    main()
