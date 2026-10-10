"""HTTP authentication, authorization, audit and security middleware."""
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse, RedirectResponse

from app.auth import SESSION_COOKIE, get_user_by_username, module_for_path, verify_token
from app.services.audit_service import record_audit


PUBLIC_PREFIXES = ("/static", "/login")
PUBLIC_EXACT = {"/health", "/favicon.ico", "/api/auth/login"}
SAFE_METHODS = {"GET", "HEAD", "OPTIONS", "TRACE"}
SENSITIVE_READ_SUFFIXES = ("/reveal", "/download")


def _path_has_prefix(path: str, prefix: str) -> bool:
    return path == prefix or path.startswith(prefix + "/")


def _forbidden(path: str):
    if path.startswith("/api"):
        return JSONResponse(status_code=403, content={"ok": False, "detail": "无权限访问该模块"})
    return RedirectResponse(url="/forbidden", status_code=307)


class AuthMiddleware(BaseHTTPMiddleware):
    """Authenticate sessions, reject cross-site writes and enforce RBAC."""

    async def dispatch(self, request, call_next):
        path = request.url.path
        if request.method not in SAFE_METHODS:
            fetch_site = (request.headers.get("sec-fetch-site") or "").lower()
            origin = request.headers.get("origin")
            host = (request.headers.get("host") or "").lower()
            foreign_origin = bool(
                origin
                and origin != "null"
                and origin.split("://", 1)[-1].lower() != host
            )
            if fetch_site == "cross-site" or foreign_origin or origin == "null":
                return JSONResponse(status_code=403, content={"detail": "跨站请求已拒绝"})

        if path in PUBLIC_EXACT or any(_path_has_prefix(path, prefix) for prefix in PUBLIC_PREFIXES):
            return await call_next(request)

        username = verify_token(request.cookies.get(SESSION_COOKIE))
        if not username:
            if path.startswith("/api"):
                return JSONResponse(status_code=401, content={"detail": "未登录或登录已过期"})
            return RedirectResponse(url="/login", status_code=307)

        user = get_user_by_username(username)
        if not user or not user.is_active:
            if path.startswith("/api"):
                return JSONResponse(status_code=401, content={"detail": "账号已被禁用或已删除，请重新登录"})
            return RedirectResponse(url="/login", status_code=307)
        request.state.user = user

        if path.startswith("/api/auth/"):
            return await call_next(request)

        module = module_for_path(path)
        if module is None:
            return await call_next(request) if user.is_superuser else _forbidden(path)
        if user.is_superuser or module in (user.get_modules() or []):
            return await call_next(request)
        return _forbidden(path)


class AuditMiddleware(BaseHTTPMiddleware):
    """Record authenticated writes and sensitive reads after they complete."""

    async def dispatch(self, request, call_next):
        response = await call_next(request)
        path = request.url.path
        should_record = (
            request.method not in SAFE_METHODS
            or any(path.endswith(suffix) for suffix in SENSITIVE_READ_SUFFIXES)
        )
        if should_record and path != "/api/auth/login":
            user = getattr(request.state, "user", None)
            parts = [part for part in path.split("/") if part]
            default_resource = parts[1] if len(parts) > 1 and parts[0] == "api" else (parts[0] if parts else "")
            resource_type = getattr(request.state, "audit_resource_type", default_resource)
            resource_id = getattr(
                request.state,
                "audit_resource_id",
                next((part for part in reversed(parts) if part.isdigit()), ""),
            )
            record_audit(
                action=getattr(
                    request.state,
                    "audit_action",
                    f"{request.method.lower()}:{resource_type or 'request'}",
                ),
                username=getattr(user, "username", "anonymous"),
                user_id=getattr(user, "id", None),
                resource_type=resource_type,
                resource_id=resource_id,
                method=request.method,
                path=path,
                status_code=response.status_code,
                success=response.status_code < 400,
                client_ip=request.client.host if request.client else "",
                detail=getattr(request.state, "audit_detail", {}),
            )
        return response


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self' 'unsafe-inline' https://unpkg.com; "
            "style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; "
            "object-src 'none'; base-uri 'self'; frame-ancestors 'none'",
        )
        return response
