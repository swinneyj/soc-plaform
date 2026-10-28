"""UI serving: the SOC_CONFIG-injecting index route + the static mount.

Split out of api/main.py (E.5). `mount(app)` must be called AFTER every
`include_router(...)` call — the catch-all StaticFiles mount at "/" would
otherwise shadow the /api/* routes.

index.modular.html is served through a small route that injects the
window.SOC_CONFIG bootstrap (O2) when the API-key mutation gate is armed:
web/utils/auth.js reads window.SOC_CONFIG.apiKey and stamps X-API-Key on every
axios request, so UI writes work without ever putting the key in the repo.
When gating is disarmed the page is served verbatim (bootstrap omitted —
nothing needs to authenticate). Every other static asset, including the
index.html redirect shim, stays on the plain mount.
"""

import json
import os

from fastapi import HTTPException
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles

WEB_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "web")

# (api_key, mtime) -> rendered html. Optimization #2 (API security review):
# the page used to be re-read from disk and re-injected with the SOC_CONFIG
# bootstrap on EVERY page load. A page load is now a cache lookup plus one
# stat, and the file is re-read + bootstrap re-injected only when the key
# changes — the file was edited on disk, or the gate armed/disarmed with a
# different key. The launcher runs a single worker, so a plain dict needs no
# lock. Tests reset `_index_cache["key"]` to force a cold cache.
_index_cache = {"key": None, "html": None}


def index_modular():
    """Serve index.modular.html with the SOC_CONFIG bootstrap when armed."""
    index_path = os.path.join(WEB_DIR, "index.modular.html")
    try:
        mtime = os.stat(index_path).st_mtime
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="UI not found")
    from api.auth import _API_KEY
    cache_key = (_API_KEY, mtime)
    if _index_cache["key"] != cache_key:
        with open(index_path, "r", encoding="utf-8") as fh:
            html = fh.read()
        if _API_KEY:
            # json.dumps keeps arbitrary key bytes safely inside the JS
            # string literal; placed before the first script tag so
            # auth.js's interceptor (loaded later) sees the config.
            bootstrap = "<script>window.SOC_CONFIG = %s;</script>\n" % json.dumps(
                {"apiKey": _API_KEY}
            )
            cut = html.find("<script")
            if cut == -1:
                html = bootstrap + html
            else:
                html = html[:cut] + bootstrap + html[cut:]
        _index_cache["key"] = cache_key
        _index_cache["html"] = html
    return Response(
        content=_index_cache["html"],
        media_type="text/html",
        headers={"Cache-Control": "no-cache"},
    )


def mount(app) -> bool:
    """Register the index route + catch-all static mount; no-op without web/."""
    if not os.path.exists(WEB_DIR):
        return False
    app.get("/index.modular.html", include_in_schema=False)(index_modular)
    app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")
    return True
