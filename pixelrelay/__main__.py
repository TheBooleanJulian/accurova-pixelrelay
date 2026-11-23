"""Run the web app: python -m pixelrelay  (HOST/PORT env vars, default 127.0.0.1:8000)."""

import os

import uvicorn

from .app import create_app


def main() -> None:
    uvicorn.run(
        create_app(),
        host=os.environ.get("HOST", "127.0.0.1"),
        port=int(os.environ.get("PORT", "8000")),
        proxy_headers=False,
    )


if __name__ == "__main__":
    main()
