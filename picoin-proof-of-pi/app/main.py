from fastapi import FastAPI

from app.api.routes import router
from app.core.settings import PROJECT_NAME
from app.db.database import init_db


app = FastAPI(
    title="Picoin Proof of Pi",
    description="MVP coordinator for Proof of Pi mining tasks.",
    version="0.1.0",
)


@app.on_event("startup")
def on_startup() -> None:
    init_db()


@app.get("/")
def root() -> dict[str, str]:
    return {"project": PROJECT_NAME, "status": "ok"}


app.include_router(router)
