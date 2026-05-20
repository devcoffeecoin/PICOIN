from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.api.routes import router
from app.core.settings import BASE_DIR, CORS_ORIGINS, PROJECT_NAME, PROTOCOL_VERSION
from app.db.database import init_db
from app.services.consensus_queue import start_consensus_queue, stop_consensus_queue
from app.services.consensus import start_replay_worker, stop_replay_worker

WEB_DIR = BASE_DIR / "app" / "web"
STATIC_DIR = WEB_DIR / "static"

app = FastAPI(
    title="Picoin Proof of Pi",
    description="MVP coordinator for Proof of Pi mining tasks.",
    version=PROTOCOL_VERSION,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=list(CORS_ORIGINS) if CORS_ORIGINS else ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
async def on_startup() -> None:
    init_db()
    await start_consensus_queue()
    await start_replay_worker()


@app.on_event("shutdown")
async def on_shutdown() -> None:
    await stop_replay_worker()
    await stop_consensus_queue()


@app.get("/")
def root() -> dict[str, str]:
    return {"project": PROJECT_NAME, "status": "ok"}


@app.get("/dashboard", include_in_schema=False)
def dashboard() -> FileResponse:
    return FileResponse(WEB_DIR / "dashboard.html")


@app.get("/dashboard/", include_in_schema=False)
def dashboard_slash() -> FileResponse:
    return dashboard()


@app.get("/wallet", include_in_schema=False)
def web_wallet() -> FileResponse:
    return FileResponse(WEB_DIR / "wallet.html")


@app.get("/wallet/", include_in_schema=False)
def web_wallet_slash() -> FileResponse:
    return web_wallet()


app.include_router(router)
app.mount("/dashboard/static", StaticFiles(directory=STATIC_DIR), name="dashboard-static")
app.mount("/wallet/static", StaticFiles(directory=STATIC_DIR), name="wallet-static")
