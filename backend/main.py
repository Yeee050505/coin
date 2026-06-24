import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")

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