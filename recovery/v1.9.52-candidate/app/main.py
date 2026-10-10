"""FastAPI application assembly.

Business routes, page routes and middleware live in dedicated modules; this
file only owns lifecycle and composition.
"""
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.auth import ensure_default_admin
from app.config import RESOURCE_DIR, settings
from app.database import engine, init_db
from app.middleware import AuditMiddleware, AuthMiddleware, SecurityHeadersMiddleware
from app.routers import (
    accounts,
    ad,
    assets,
    auth_users,
    backups,
    commands,
    credentials,
    dashboard,
    dc,
    dhcp,
    devices,
    ip_inventory,
    ipam,
    integrations,
    logs,
    nics,
    pages,
    schedule,
    serial,
    system,
    topology,
    vms,
)
from app.scheduler.tasks import init_scheduler, shutdown_scheduler
from app.services.command_config import ensure_commands_file
from app.services.log_service import prune_configured_history


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting %s v%s", settings.APP_NAME, settings.APP_VERSION)
    init_db()
    ensure_default_admin()
    ensure_commands_file()
    pruned = prune_configured_history()
    if any(pruned.values()):
        logger.info("Operational history retention removed: %s", pruned)
    init_scheduler()
    logger.info("Application started successfully")
    try:
        yield
    finally:
        shutdown_scheduler()
        engine.dispose()
        logger.info("Application shutdown")


app = FastAPI(title=settings.APP_NAME, version=settings.APP_VERSION, lifespan=lifespan)


@app.exception_handler(RequestValidationError)
async def request_validation_error(_request: Request, exc: RequestValidationError):
    """Return validation locations/messages without echoing submitted secrets."""
    details = []
    for error in exc.errors():
        clean = {key: value for key, value in error.items() if key not in {"input", "url"}}
        if isinstance(clean.get("ctx"), dict):
            clean["ctx"] = {
                key: str(value) for key, value in clean["ctx"].items() if key != "given"
            }
        details.append(clean)
    return JSONResponse(status_code=422, content={"detail": details})

# Starlette executes the last-added middleware first. Security headers remain
# outermost, audit observes authorization results, and auth guards the routes.
app.add_middleware(AuthMiddleware)
app.add_middleware(AuditMiddleware)
app.add_middleware(SecurityHeadersMiddleware)

app.mount("/static", StaticFiles(directory=str(RESOURCE_DIR / "static")), name="static")

# Literal /api/backups/data routes must precede /api/backups/{backup_id}.
for router in (
    backups.app_data_router,
    devices.router,
    backups.router,
    topology.router,
    dashboard.router,
    schedule.router,
    serial.router,
    commands.router,
    nics.router,
    credentials.router,
    ip_inventory.router,
    ipam.router,
    dhcp.router,
    dhcp.ipam_router,
    dc.router,
    assets.router,
    accounts.router,
    ad.router,
    vms.router,
    integrations.router,
    logs.router,
    system.router,
    auth_users.router,
    pages.router,
):
    app.include_router(router)
