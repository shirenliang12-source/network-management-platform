"""Server-rendered management pages and public health endpoint."""
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app.auth import MODULES, MODULE_KEYS
from app.config import RESOURCE_DIR, settings


router = APIRouter()
templates = Jinja2Templates(directory=str(RESOURCE_DIR / "templates"))
templates.env.globals.update(
    APP_VERSION=settings.APP_VERSION,
    MODULES=MODULES,
    MODULE_KEYS=MODULE_KEYS,
)


def _page(template_name: str):
    async def render(request: Request):
        return templates.TemplateResponse(request, template_name, {"app_name": settings.APP_NAME})
    render.__name__ = f"{template_name.removesuffix('.html')}_page"
    return render


router.add_api_route("/", _page("dashboard.html"), response_class=HTMLResponse, methods=["GET"])
router.add_api_route("/devices", _page("devices.html"), response_class=HTMLResponse, methods=["GET"])
router.add_api_route("/topology", _page("topology.html"), response_class=HTMLResponse, methods=["GET"])
router.add_api_route("/backups", _page("backups.html"), response_class=HTMLResponse, methods=["GET"])
router.add_api_route("/changes", _page("changes.html"), response_class=HTMLResponse, methods=["GET"])
router.add_api_route("/settings", _page("settings.html"), response_class=HTMLResponse, methods=["GET"])
router.add_api_route("/schedule", _page("schedule.html"), response_class=HTMLResponse, methods=["GET"])
router.add_api_route("/ip-inventory", _page("ip_inventory.html"), response_class=HTMLResponse, methods=["GET"])
router.add_api_route("/ipam", _page("ipam.html"), response_class=HTMLResponse, methods=["GET"])
router.add_api_route("/datacenter", _page("dc.html"), response_class=HTMLResponse, methods=["GET"])
router.add_api_route("/assets", _page("assets.html"), response_class=HTMLResponse, methods=["GET"])
router.add_api_route("/servers", _page("servers.html"), response_class=HTMLResponse, methods=["GET"])
router.add_api_route("/accounts", _page("accounts.html"), response_class=HTMLResponse, methods=["GET"])
router.add_api_route("/vms", _page("vms.html"), response_class=HTMLResponse, methods=["GET"])
router.add_api_route("/integrations", _page("integrations.html"), response_class=HTMLResponse, methods=["GET"])


@router.get("/devices/{device_id}", response_class=HTMLResponse)
async def device_detail_page(request: Request, device_id: int):
    return templates.TemplateResponse(
        request,
        "device_detail.html",
        {"app_name": settings.APP_NAME, "device_id": device_id},
    )


@router.get("/health")
async def health():
    return {"status": "ok", "app": settings.APP_NAME, "version": settings.APP_VERSION}
