import os
import uvicorn


def start() -> None:
    host = os.getenv("HOST", "0.0.0.0")
    port = int(os.getenv("PORT", "7979"))
    uvicorn.run("scavengarr.application.main:app", host=host, port=port)
