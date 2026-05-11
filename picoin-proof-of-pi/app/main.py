from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.api.routes import router
from app.core.settings import BASE_DIR, PROJECT_NAME, PROTOCOL_VERSION
from app.db.database import init_db

WEB_DIR = BASE_DIR / "app" / "web"
STATIC_DIR = WEB_DIR / "static"

app = FastAPI(
    title="Picoin Proof of Pi",
    description="MVP coordinator for Proof of Pi mining tasks.",
    version=PROTOCOL_VERSION,
)


@app.on_event("startup")
def on_startup() -> None:
    init_db()


@app.get("/")
def root() -> dict[str, str]:
    return {"project": PROJECT_NAME, "status": "ok"}


@app.get("/dashboard", include_in_schema=False)
def dashboard() -> FileResponse:
    return FileResponse(WEB_DIR / "dashboard.html")


app.include_router(router)
app.mount("/dashboard/static", StaticFiles(directory=STATIC_DIR), name="dashboard-static")
