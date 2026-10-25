"""Serves the website: one shared layout + page fragments, security headers, robots/sitemap and a real 404.

Page files live in frontend/pages/<name>.html and start with a meta comment, e.g.
    <!--meta title="How it works" description="..." script="how.js" nav="how" robots="noindex" bodyclass="app" -->
The layout (frontend/layout.html) supplies the header, footer and safety banner, so they can't drift between pages.
The two dashboards are guarded on the server: a signed-out browser never receives the dashboard HTML.
"""
from __future__ import annotations

import html
import os
import re
from datetime import datetime
from typing import Dict, Tuple
from urllib.parse import quote, urlparse

from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from src.api.deps import session_optional
from src.config import get_settings

SET = get_settings()
FRONT = SET.frontend_dir
router = APIRouter()

PUBLIC: Dict[str, str] = {"/": "home", "/how-it-works": "how-it-works", "/library": "library", "/research": "research", "/about": "about",
                          "/contact": "contact", "/privacy": "privacy", "/terms": "terms", "/accessibility": "accessibility"}
AUTH: Dict[str, str] = {"/login": "login", "/register": "register", "/register/clinician": "register-clinician",
                        "/forgot-password": "forgot-password", "/reset-password": "reset-password"}
APP: Dict[str, Tuple[str, str]] = {"/portal": ("portal", "patient"), "/clinician": ("clinician", "clinician")}

_META_RE = re.compile(r"\A\s*<!--\s*meta\s+(.*?)-->", re.S)
_KV_RE = re.compile(r'(\w+)="(.*?)"', re.S)
_cache: Dict[str, str] = {}


def _read(rel: str) -> str:
    if SET.app_env != "development" and rel in _cache:
        return _cache[rel]
    text = (FRONT / rel).read_text(encoding="utf-8")
    _cache[rel] = text
    return text


def _asset_version() -> str:
    """Changes whenever an asset file changes, so browsers never run stale JS/CSS."""
    newest = 0.0
    for root, _, files in os.walk(FRONT / "assets"):
        for f in files:
            if not f.endswith(".woff2"):
                newest = max(newest, os.path.getmtime(os.path.join(root, f)))
    return str(int(newest))


_ver_cache: Dict[str, str] = {}


def _ver() -> str:
    if SET.app_env == "development" or "v" not in _ver_cache:
        _ver_cache["v"] = _asset_version()
    return _ver_cache["v"]


def home_for(role: str) -> str:
    return "/clinician" if role == "clinician" else "/portal"


def render(page: str, request: Request, status: int = 200) -> HTMLResponse:
    raw = _read(f"pages/{page}.html")
    m = _META_RE.match(raw)
    meta = dict(_KV_RE.findall(m.group(1))) if m else {}
    body = raw[m.end():] if m else raw
    base = SET.public_url or str(request.base_url).rstrip("/")
    title = meta.get("title", SET.site_name)
    full_title = f"{SET.site_name}: {meta['tagline']}" if meta.get("tagline") else f"{title} | {SET.site_name}"
    scripts = "".join(f'<script type="module" src="/assets/js/{s}?v={_ver()}"></script>' for s in meta.get("script", "").split() if s)
    robots = meta.get("robots", "index,follow")
    nav = meta.get("nav", "")
    out = _read("layout.html")
    repl = {"{{title}}": html.escape(full_title), "{{description}}": html.escape(meta.get("description", "")), "{{robots}}": robots,
            "{{canonical}}": html.escape(base + request.url.path), "{{bodyclass}}": meta.get("bodyclass", ""), "{{scripts}}": scripts,
            "{{ver}}": _ver(), "{{year}}": str(datetime.now().year), "{{content}}": body}
    for k, v in repl.items():
        out = out.replace(k, v)
    if nav:
        out = out.replace(f'data-nav="{nav}"', f'data-nav="{nav}" aria-current="page"')
    out = out.replace("{{site_name}}", html.escape(SET.site_name))
    resp = HTMLResponse(out, status_code=status)
    if page in ("portal", "clinician") or page in AUTH.values():
        resp.headers["Cache-Control"] = "no-store"
    return resp


def _public_handler(page: str):
    def handler(request: Request):
        return render(page, request)
    return handler


def _auth_handler(page: str):
    def handler(request: Request):
        sess = session_optional(request)
        if sess is not None and page != "reset-password":
            return RedirectResponse(home_for(sess.user.role), status_code=302)
        return render(page, request)
    return handler


def _app_handler(page: str, role: str, path: str):
    def handler(request: Request):
        sess = session_optional(request)
        if sess is None:
            return RedirectResponse(f"/login?role={role}&next={quote(path)}", status_code=302)
        if sess.user.role != role:
            return RedirectResponse(home_for(sess.user.role), status_code=302)
        return render(page, request)
    return handler


for _path, _page in PUBLIC.items():
    router.add_api_route(_path, _public_handler(_page), methods=["GET"], include_in_schema=False)
for _path, _page in AUTH.items():
    router.add_api_route(_path, _auth_handler(_page), methods=["GET"], include_in_schema=False)
for _path, (_page, _role) in APP.items():
    router.add_api_route(_path, _app_handler(_page, _role, _path), methods=["GET"], include_in_schema=False)


@router.get("/favicon.ico", include_in_schema=False)
def favicon():
    return FileResponse(FRONT / "assets" / "img" / "favicon.svg", media_type="image/svg+xml")


@router.get("/robots.txt", include_in_schema=False)
def robots(request: Request):
    base = SET.public_url or str(request.base_url).rstrip("/")
    return PlainTextResponse("User-agent: *\nAllow: /\nDisallow: /api/\nDisallow: /portal\nDisallow: /clinician\nDisallow: /login\n"
                             "Disallow: /register\nDisallow: /forgot-password\nDisallow: /reset-password\n\n" f"Sitemap: {base}/sitemap.xml\n")


@router.get("/sitemap.xml", include_in_schema=False)
def sitemap(request: Request):
    base = SET.public_url or str(request.base_url).rstrip("/")
    urls = "".join(f"<url><loc>{html.escape(base + p)}</loc></url>" for p in PUBLIC)
    return HTMLResponse(f'<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{urls}</urlset>',
                        media_type="application/xml")


# ----------------------------------------------------------------------------- hardening + errors
_CSP = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; font-src 'self'; connect-src 'self'; "
        "frame-ancestors 'none'; base-uri 'self'; form-action 'self'; object-src 'none'")
_CSP_DOCS = "default-src 'self'; script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; img-src 'self' data: https://fastapi.tiangolo.com; frame-ancestors 'none'"
_UNSAFE = {"POST", "PUT", "PATCH", "DELETE"}


def install(app: FastAPI) -> None:
    app.include_router(router)

    @app.middleware("http")
    async def harden(request: Request, call_next):
        # Same-origin check for state-changing API calls (defence in depth alongside SameSite cookies and CSRF tokens).
        if request.method in _UNSAFE and request.url.path.startswith("/api/"):
            origin = request.headers.get("origin")
            if origin and urlparse(origin).netloc != request.headers.get("host", ""):
                return JSONResponse({"detail": "Cross-site request blocked."}, status_code=403)
        resp = await call_next(request)
        h = resp.headers
        h["X-Content-Type-Options"] = "nosniff"
        h["X-Frame-Options"] = "DENY"
        h["Referrer-Policy"] = "strict-origin-when-cross-origin"
        h["Permissions-Policy"] = "camera=(), microphone=(), geolocation=(), payment=()"
        h["Content-Security-Policy"] = _CSP_DOCS if request.url.path.startswith(("/docs", "/redoc")) else _CSP
        if SET.cookie_secure:
            h["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        if request.url.path.startswith("/api/") and "cache-control" not in h:
            h["Cache-Control"] = "no-store"
        return resp

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException):
        if exc.status_code == 404 and not request.url.path.startswith(("/api/", "/assets/")) and "text/html" in request.headers.get("accept", ""):
            return render("404", request, status=404)
        return JSONResponse({"detail": exc.detail}, status_code=exc.status_code, headers=getattr(exc, "headers", None))
