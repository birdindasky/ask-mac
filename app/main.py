"""FastAPI app — wires routers, serves static frontend."""
from __future__ import annotations
import hashlib
import os
import sys
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

from . import db, settings
from .api import admin as admin_api
from .api import budget as budget_api
from .api import chat as chat_api
from .api import internal as internal_api
from .api import providers as providers_api
from .api import sessions as sessions_api
from .api import ui_prefs as ui_prefs_api
from .api import web_search as web_search_api
from .providers import echo_dev
from .utils.logging_setup import init_logging

init_logging()
echo_dev.maybe_register()


def _resolve_static_dir() -> Path:
    """Find static/ in dev (repo root) or .app bundle (Contents/Resources/)."""
    # Dev: app/main.py → repo_root/static
    candidates = [Path(__file__).resolve().parent.parent / "static"]
    # py2app data_files lands at Contents/Resources/static; sys.executable lives
    # in Contents/MacOS, so its great-grandparent is Resources.
    exe = Path(sys.executable).resolve()
    candidates.append(exe.parent.parent / "Resources" / "static")
    # Final fallback: env override (useful for tests).
    env = os.environ.get("MLC_STATIC_DIR")
    if env:
        candidates.insert(0, Path(env))
    for c in candidates:
        if (c / "index.html").is_file():
            return c
    # Surface a clear error rather than letting StaticFiles' message win.
    raise RuntimeError(f"static/ not found (tried {[str(c) for c in candidates]})")


STATIC_DIR = _resolve_static_dir()


def _asset_buster() -> str:
    """Stable per-build cache-bust token: sha1 of app.js + style.css contents.
    WKWebView aggressively caches /static/* across .app reinstalls when the
    URL is identical; appending ?v=<hash> guarantees a fresh fetch when we
    ship updated frontend bytes."""
    h = hashlib.sha1()
    for name in ("app.js", "i18n.js", "style.css", "index.html"):
        p = STATIC_DIR / name
        if p.is_file():
            h.update(p.read_bytes())
    return h.hexdigest()[:10]


_ASSET_BUSTER = _asset_buster()


app = FastAPI(title="Multi-LLM Chat by Claude", version=settings.APP_VERSION)


@app.middleware("http")
async def _no_cache_static(request: Request, call_next):
    """Force /static/* to revalidate every load. Cache-Control: no-store
    means WKWebView treats every fetch as fresh — small bandwidth cost in
    exchange for guaranteed-correct frontend after every .app upgrade."""
    response = await call_next(request)
    if request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-store, must-revalidate"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response

# Initialize DB at startup (fail fast if path is broken).
db.get_conn()


@app.get("/api/health")
async def health():
    return {"ok": True}


app.include_router(providers_api.router)
app.include_router(sessions_api.router)
app.include_router(chat_api.router)
app.include_router(ui_prefs_api.router)
app.include_router(web_search_api.router)
app.include_router(budget_api.router)
app.include_router(admin_api.router)
app.include_router(internal_api.router)


@app.get("/")
async def index():
    """Inject a build-hash query param onto every /static/ asset reference
    in index.html so cached older bytes can never serve in place of the new
    ones after an .app upgrade."""
    raw = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    rewritten = raw.replace('href="/static/style.css"', f'href="/static/style.css?v={_ASSET_BUSTER}"')
    rewritten = rewritten.replace('src="/static/app.js"', f'src="/static/app.js?v={_ASSET_BUSTER}"')
    rewritten = rewritten.replace('src="/static/i18n.js"', f'src="/static/i18n.js?v={_ASSET_BUSTER}"')
    return HTMLResponse(rewritten, headers={"Cache-Control": "no-store"})


app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
