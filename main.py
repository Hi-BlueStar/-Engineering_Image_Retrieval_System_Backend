import logging

from fastapi import FastAPI, Request
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles

from src import demo_router

# 設定系統日誌格式
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# 初始化 FastAPI 應用程式
app = FastAPI(
    title="工程圖影像檢索系統 API",
    description="提供影像檢索、批次取得與 WebSocket 進度推送的 API",
    version="1.0.0"
)

app.mount(
    "/api/v1/static",
    StaticFiles(directory="data/engineering_images_Clean_100dpi"),
    name="static"
)

# 註冊 API 路由與 WebSocket 路由
app.include_router(demo_router)

# 初始化 Jinja2 模板引擎
templates = Jinja2Templates(directory="templates")

@app.get("/")
async def serve_frontend(request: Request):
    """
    提供展示用前端網頁。
    """
    return templates.TemplateResponse("index.html", {"request": request})

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=True)
