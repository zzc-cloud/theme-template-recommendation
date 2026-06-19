"""静态资源挂载。

interaction-console 前端是无构建链的静态页面：FastAPI 直接暴露 `web/assets`，并把
`web/index.html` 作为首页返回。
"""

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .config import WEB_DIR


def mount_static(app: FastAPI) -> None:
    """把 web/assets 挂载到 /assets，供 HTML、CSS、JS 和图片访问。"""
    assets_dir = WEB_DIR / "assets"
    app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")


async def index() -> FileResponse:
    """返回单页控制台入口。"""
    return FileResponse(WEB_DIR / "index.html")
