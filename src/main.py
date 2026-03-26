"""
應用程式進入點 (Application Bootstrap)。
彙整各層服務、註冊路由、設定中介軟體 (CORS) 以及靜態目錄與前端樣板服務。
"""

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from src.router import get_feature_extractor, get_vector_repo
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError

from src.router import router as demo_router

import logging


# =============================================================================
# 應用程式全局配置 (Application Configuration)
# =============================================================================
# 可透過環境變數或直接在此修改預設值
APP_CONFIG = {
    "db_path": os.getenv("DB_PATH", "./chroma_db"),
    "collection_name": os.getenv("COLLECTION_NAME", "engineering_drawings"),
    "model_path": os.getenv("MODEL_PATH", "./model/checkpoint_best.pth"),  # e.g., "weights/simsiam_best.pth"
    "device": os.getenv("DEVICE", "cuda"), # e.g., "cuda" or "cpu"
    "debug": os.getenv("DEBUG", "False").lower() in ("true", "1", "yes"),
}


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    LifeSpan 事件管理：啟動伺服器時預先載入依賴，關閉時清理。
    提供應用系統啟動時載入參數的統一入口。
    """
    logger = logging.getLogger("demo.main")
    if APP_CONFIG["debug"]:
        logging.basicConfig(level=logging.DEBUG)
        logger.debug(f"啟動配置: {APP_CONFIG}")

    # 預載入向量庫與特徵提取模型
    get_vector_repo(
        db_path=APP_CONFIG["db_path"],
        collection_name=APP_CONFIG["collection_name"]
    )
    get_feature_extractor(
        model_path=APP_CONFIG["model_path"],
        device=APP_CONFIG["device"]
    )
    yield
    print("Application Shutdown...")


app = FastAPI(
    title="Engineering Image Retrieval System",
    description="基於 SimSiam 與 ChromaDB 的工程圖特徵檢索系統",
    version="1.0.0",
    lifespan=lifespan,
)

# =============================================================================
# Middleware (CORS)
# =============================================================================
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 開發環境允許所有來源，生產環境應做限制
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# =============================================================================
# 路由註冊 (Router Registration)
# =============================================================================
app.include_router(demo_router)

# =============================================================================
# 靜態檔案與樣板設定 (Static Files & Templates)
# =============================================================================
STATIC_DIR = "static"
IMAGES_DIR = os.path.join(STATIC_DIR, "images")
os.makedirs(IMAGES_DIR, exist_ok=True)

# 掛載靜態檔案目錄 (用於前端取得檢索圖片)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

# Jinja2 樣板目錄
templates = Jinja2Templates(directory="templates")


@app.get("/", summary="前端互動測試頁面", tags=["Frontend"])
async def serve_frontend(request: Request):
    """提供系統開發使用的前端網頁 (Jinja2)"""
    response = templates.TemplateResponse("index.html", {"request": request})
    # 設定禁用快取，確保前端隨時拉取最新修正的 WebSocket 連線版腳本
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    """
    全域攔截 RequestValidationError，重塑 422 錯誤的輸出格式。
    """
    # exc.errors() 會回傳 Pydantic 原始的驗證錯誤清單
    original_errors = exc.errors()
    
    # 建構前瞻性的標準化錯誤格式
    custom_error_format = {
        "status": "error",
        "error_code": "VALIDATION_FAILED",
        "detail": "請求參數資料格式錯誤或是超過範圍。",
        # 將錯誤節點扁平化並重新命名，提升客戶端解析的友善度
        "invalid_fields": [
            {
                "field": " -> ".join(str(loc) for loc in err.get("loc", [])),
                "reason": err.get("msg"),
                "error_type": err.get("type")
            }
            for err in original_errors
        ]
    }

    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content=custom_error_format
    )

if __name__ == "__main__":
    import uvicorn

    # 本地測試快速啟動方式
    uvicorn.run("src.main:app", host="0.0.0.0", port=8000, reload=True)
