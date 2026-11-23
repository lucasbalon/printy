from __future__ import annotations

import asyncio
import logging
import os
import shutil
from contextlib import asynccontextmanager, suppress
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from .api import jobs, library, octoprint, printers, settings, spools, stock
from .config import config
from .db import init_db
from .ha import ha
from .printers import manager
from .scheduler import scheduler
from .watcher import watch_inbox

logging.basicConfig(
    level=os.environ.get("PRINTY_LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("printy")

STATIC_DIR = Path(__file__).parent / "static"
INGRESS_PROXY = "172.30.32.2"
DIRECT_PORT_PATHS = ("/api/version", "/api/files/local")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    shutil.rmtree(config.library_dir / ".tmp", ignore_errors=True)  # interrupted uploads
    await manager.sync()
    scheduler.start()
    watcher = asyncio.create_task(watch_inbox(), name="inbox")
    log.info("Printy started (Home Assistant: %s)", config.in_home_assistant)
    yield
    watcher.cancel()
    with suppress(asyncio.CancelledError):
        await watcher
    await scheduler.stop()
    await manager.close()
    await ha.close()


app = FastAPI(title="Printy", lifespan=lifespan, docs_url="/api/docs" if config.dev else None)


@app.middleware("http")
async def restrict_direct_port(request: Request, call_next):
    """The interface is only served through Home Assistant Ingress (which handles
    authentication). The port opened on the network only accepts slicer uploads."""
    if config.in_home_assistant:
        client = request.client.host if request.client else ""
        if client != INGRESS_PROXY and request.url.path not in DIRECT_PORT_PATHS:
            return JSONResponse({"detail": "Interface disponible depuis Home Assistant."}, status_code=403)
    return await call_next(request)


for module in (library, printers, jobs, settings, spools, stock):
    app.include_router(module.router, prefix="/api/v1")
app.include_router(octoprint.router)


@app.get("/api/v1/health")
def health():
    return {"ok": True, "home_assistant": config.in_home_assistant}


if config.dev:

    @app.post("/api/v1/dev/demo-print")
    async def dev_demo_print(printer_id: int, hours: float = 2, name: str = "vase_test.gcode"):
        """Development only: starts a print on a demo printer as if launched from its screen."""
        from .drivers.demo import DemoDriver

        driver = manager.driver(printer_id)
        if not isinstance(driver, DemoDriver):
            return JSONResponse({"detail": "Imprimante de démonstration seulement."}, status_code=400)
        driver.files[name] = int(hours * 3600)
        await driver.start(name)
        return {"ok": True}


if STATIC_DIR.exists():
    app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="ui")
