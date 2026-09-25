import logging
import os
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
# 进程 stderr 被外部重定向后可能静默失效, 日志落盘保证可追溯
try:
    _log_dir = os.path.join(os.path.dirname(__file__), "logs")
    os.makedirs(_log_dir, exist_ok=True)
    logging.getLogger().addHandler(
        logging.FileHandler(os.path.join(_log_dir, "app.log"), encoding="utf-8"))
except Exception:
    pass

from app.models import init_db
from app.api import projects, reports, dashboard

@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    logging.info("Financial Research Agent started on port 8001 - main.py:14")
    yield

app = FastAPI(title="Financial Deep Research Agent", version="1.0.0", lifespan=lifespan)

app.include_router(projects.router)
app.include_router(reports.router)
app.include_router(dashboard.router)

import os
frontend_dist = os.path.join(os.path.dirname(__file__), "..", "frontend", "dist")
if os.path.isdir(frontend_dist):
    app.mount("/", StaticFiles(directory=frontend_dist, html=True), name="frontend")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="127.0.0.1", port=8001)