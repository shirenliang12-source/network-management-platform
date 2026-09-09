"""Login, session and management-user API routes."""
import threading
import time

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from app.api_models import (
    LoginRequest,
    PasswordChangeRequest,
    UserCreateRequest,
    UserUpdateRequest,
)
from app.auth import (
    MODULE_KEYS,
    SESSION_COOKIE,
    SESSION_MAX_AGE,
    authenticate,
    count_active_superusers,
    create_user,
    delete_user,
    get_user_by_username,
    list_users,
    make_token,
    update_user,
    verify_password,
    verify_token,
)
from app.config import settings
from app.database import SessionLocal
from app.models import User
from app.routers.pages import templates
from app.services.audit_service import record_audit


router = APIRouter()
_LOGIN_WINDOW_SECONDS = 5 * 60
_LOGIN_MAX_FAILURES = 5
_LOGIN_MAX_TRACKED_KEYS = 10_000
_login_failures = {}
_login_lock = threading.Lock()


def _set_session_cookie(response, username: str):
    response.set_cookie(
        SESSION_COOKIE,
        make_token(username),
        max_age=SESSION_MAX_AGE,
        httponly=True,
        secure=settings.COOKIE_SECURE,
        samesite="lax",
        path="/",
    )


def _login_key(request: Request, username: str):
    client_ip = request.client.host if request.client else "unknown"
    return client_ip, username.casefold()


def _login_retry_after(request: Request, username: str) -> int:
    now = time.monotonic()
    key = _login_key(request, username)
    with _login_lock:
        recent = [stamp for stamp in _login_failures.get(key, []) if now - stamp < _LOGIN_WINDOW_SECONDS]
        if recent:
            _login_failures[key] = recent
        else:
            _login_failures.pop(key, None)
        if len(recent) < _LOGIN_MAX_FAILURES:
            return 0
        return max(1, int(_LOGIN_WINDOW_SECONDS - (now - recent[0])))


def _record_login_result(request: Request, username: str, success: bool):
    key = _login_key(request, username)
    with _login_lock:
        if success:
            _login_failures.pop(key, None)
            return
        if key not in _login_failures and len(_login_failures) >= _LOGIN_MAX_TRACKED_KEYS:
            now = time.monotonic()
            expired = [
                item_key
                for item_key, stamps in _login_failures.items()
                if not stamps or now - stamps[-1] >= _LOGIN_WINDOW_SECONDS
            ]
            for item_key in expired:
                _login_failures.pop(item_key, None)
            if len(_login_failures) >= _LOGIN_MAX_TRACKED_KEYS:
                _login_failures.pop(next(iter(_login_failures)))
        _login_failures.setdefault(key, []).append(time.monotonic())


def _audit_login(request: Request, username: str, status_code: int, reason: str, user_id=None):
    record_audit(
        action="auth.login",
        username=username,
        user_id=user_id,
        resource_type="session",
        method="POST",
        path="/api/auth/login",
        status_code=status_code,
        success=status_code < 400,
        client_ip=request.client.host if request.client else "",
        detail={"reason": reason},
    )


def _require_superuser(request: Request) -> User:
    user = getattr(request.state, "user", None)
    if not user or not user.is_superuser:
        raise HTTPException(status_code=403, detail="仅超级管理员可操作")
    return user


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    username = verify_token(request.cookies.get(SESSION_COOKIE))
    user = get_user_by_username(username) if username else None
    if user and user.is_active:
        return RedirectResponse(url="/")
    response = templates.TemplateResponse(request, "login.html", {"app_name": settings.APP_NAME})
    if request.cookies.get(SESSION_COOKIE):
        response.delete_cookie(SESSION_COOKIE, path="/")
    return response


@router.post("/api/auth/login")
async def auth_login(request: Request, payload: LoginRequest):
    retry_after = _login_retry_after(request, payload.username)
    if retry_after:
        _audit_login(request, payload.username, 429, "rate_limited")
        return JSONResponse(
            status_code=429,
            content={"ok": False, "detail": "登录失败次数过多，请稍后重试"},
            headers={"Retry-After": str(retry_after)},
        )
    user = authenticate(payload.username, payload.password)
    if not user:
        _record_login_result(request, payload.username, False)
        _audit_login(request, payload.username, 401, "invalid_credentials")
        return JSONResponse(status_code=401, content={"ok": False, "detail": "账号或密码错误"})
    _record_login_result(request, payload.username, True)
    _audit_login(request, user.username, 200, "success", user.id)
    response = JSONResponse(content={"ok": True, "username": user.username, "is_superuser": user.is_superuser})
    _set_session_cookie(response, user.username)
    return response


@router.post("/api/auth/logout")
async def auth_logout():
    response = JSONResponse(content={"ok": True})
    response.delete_cookie(SESSION_COOKIE, path="/")
    return response


@router.get("/api/auth/me")
async def auth_me(request: Request):
    user = getattr(request.state, "user", None)
    if not user:
        raise HTTPException(status_code=401, detail="未登录")
    return {
        "username": user.username,
        "is_superuser": user.is_superuser,
        "is_active": user.is_active,
        "modules": user.get_modules(),
    }


@router.post("/api/auth/set")
async def auth_set(request: Request, payload: PasswordChangeRequest):
    user = getattr(request.state, "user", None)
    if not user:
        raise HTTPException(status_code=401, detail="未登录")
    if not verify_password(payload.current_password, user.password_hash):
        raise HTTPException(status_code=401, detail="当前密码不正确")
    new_username = payload.username or user.username
    if payload.password != payload.confirm_password:
        raise HTTPException(status_code=400, detail="两次输入的新密码不一致")
    if payload.password and len(payload.password) < 12:
        raise HTTPException(status_code=400, detail="新密码至少需要 12 个字符")
    existing = get_user_by_username(new_username)
    if existing and existing.id != user.id:
        raise HTTPException(status_code=400, detail="该账号名已被占用")
    update_user(user.id, username=new_username, password=payload.password)
    request.state.audit_action = "user.change_own_credentials"
    request.state.audit_resource_type = "user"
    request.state.audit_resource_id = user.id
    request.state.audit_detail = {
        "username_changed": new_username != user.username,
        "password_changed": bool(payload.password),
    }
    response = JSONResponse(content={"ok": True, "detail": "登录口令已更新"})
    _set_session_cookie(response, new_username)
    return response


@router.get("/forbidden", response_class=HTMLResponse)
async def forbidden_page():
    html = """<!DOCTYPE html><html lang="zh-CN"><head><meta charset="UTF-8">
<title>无访问权限</title><link rel="stylesheet" href="/static/css/style.css?v={version}"></head>
<body style="display:flex;align-items:center;justify-content:center;height:100vh;margin:0;background:#f4f6f9;font-family:sans-serif;">
<div style="text-align:center;padding:40px;background:#fff;border-radius:10px;box-shadow:0 2px 10px rgba(0,0,0,.1);">
<div style="font-size:54px;">🚫</div><h2>无访问权限</h2>
<p style="color:#666;">您的账号未被授权访问该模块，请联系超级管理员开通。</p>
<a href="/" class="btn btn-primary" style="text-decoration:none;display:inline-block;margin-top:10px;">返回首页</a>
</div></body></html>""".format(version=settings.APP_VERSION)
    return HTMLResponse(html)


@router.get("/api/users")
async def api_list_users(request: Request):
    _require_superuser(request)
    return {"ok": True, "users": list_users(), "module_keys": MODULE_KEYS}


@router.post("/api/users")
async def api_create_user(request: Request, payload: UserCreateRequest):
    _require_superuser(request)
    if get_user_by_username(payload.username):
        raise HTTPException(status_code=400, detail="该账号已存在")
    created = create_user(
        payload.username,
        payload.password,
        payload.is_superuser,
        payload.is_active,
        payload.modules,
    )
    request.state.audit_action = "user.create"
    request.state.audit_resource_type = "user"
    request.state.audit_resource_id = created.id
    request.state.audit_detail = {"target_username": created.username, "is_superuser": created.is_superuser}
    return {"ok": True, "detail": "用户已创建"}


@router.put("/api/users/{user_id}")
async def api_update_user(request: Request, user_id: int, payload: UserUpdateRequest):
    _require_superuser(request)
    if payload.username:
        same_name = get_user_by_username(payload.username)
        if same_name and same_name.id != user_id:
            raise HTTPException(status_code=400, detail="该账号名已被其他用户占用")

    with SessionLocal() as db:
        current = db.get(User, user_id)
        if current is None:
            raise HTTPException(status_code=404, detail="用户不存在")
        is_superuser = current.is_superuser if payload.is_superuser is None else payload.is_superuser
        is_active = current.is_active if payload.is_active is None else payload.is_active
        would_remove_active_admin = (
            current.is_superuser
            and current.is_active
            and (not is_superuser or not is_active)
        )
    if would_remove_active_admin and count_active_superusers() <= 1:
        raise HTTPException(status_code=400, detail="至少需保留一个启用的超级管理员")

    fields = {}
    for name in ("username", "password", "is_superuser", "is_active", "modules"):
        value = getattr(payload, name)
        if value is not None and not (name == "password" and value == ""):
            fields[name] = value
    try:
        updated = update_user(user_id, **fields)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    request.state.audit_action = "user.update"
    request.state.audit_resource_type = "user"
    request.state.audit_resource_id = user_id
    request.state.audit_detail = {
        "target_username": updated.username,
        "changed_fields": sorted(fields),
    }
    return {"ok": True, "detail": "用户已更新"}


@router.delete("/api/users/{user_id}")
async def api_delete_user(request: Request, user_id: int):
    current_user = _require_superuser(request)
    if current_user.id == user_id:
        raise HTTPException(status_code=400, detail="不能删除当前登录的账号")
    with SessionLocal() as db:
        target = db.get(User, user_id)
        removes_active_admin = bool(target and target.is_superuser and target.is_active)
        target_username = target.username if target else ""
    if removes_active_admin and count_active_superusers() <= 1:
        raise HTTPException(status_code=400, detail="至少需保留一个启用的超级管理员")
    try:
        delete_user(user_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    request.state.audit_action = "user.delete"
    request.state.audit_resource_type = "user"
    request.state.audit_resource_id = user_id
    request.state.audit_detail = {"target_username": target_username}
    return {"ok": True, "detail": "用户已删除"}
