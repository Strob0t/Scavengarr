from __future__ import annotations

from typing import Iterable, Optional

from fastapi import FastAPI

app = FastAPI()


@app.get("/")
async def root():
    return {"message": "Hello World"}


def main(argv: Optional[Iterable[str]] = None) -> int:
    # Placeholder, kann später CLI-Subcommands bekommen.
    return 0
