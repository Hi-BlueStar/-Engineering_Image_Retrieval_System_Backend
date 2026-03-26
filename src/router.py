"""
API 路由層 (Router Layer)。
負責定義 HTTP 端點、解析 Request 參數並將其傳遞給業務邏輯層 (Service)，
維持遵循 Layer 1 傳輸層原則，不包含複雜業務邏輯判斷。
"""

import json
import logging
import uuid
import os
from typing import Annotated

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    Form,
    HTTPException,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
)
from src.repo import VectorRepository
from src.schemas import (
    CategoryListResponse,
    RetrievalConditions,
    RetrievalResponse,
    UploadResponse,
)
from src.service import (
    RetrievalService,
    process_dataset_background,
    ws_manager,
)
from src.vector_search.feature_extractor import SimSiamFeatureExtractor

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Engineering Drawing Image Retrieval API"])

# =============================================================================
# 依賴注入 (Dependencies)
# =============================================================================

# 單例模式載入資料庫與模型
_vector_repo: VectorRepository | None = None
_feature_extractor: SimSiamFeatureExtractor | None = None


def get_vector_repo(
    db_path: str = "./chroma_db",
    collection_name: str = "engineering_drawings"
) -> VectorRepository:
    """依賴注入: 取得向量資料庫實例。"""
    global _vector_repo
    if _vector_repo is None:
        _vector_repo = VectorRepository(db_path=db_path, collection_name=collection_name)
    return _vector_repo


def get_feature_extractor(
    model_path: str | None = None,
    device: str = "cpu"
) -> SimSiamFeatureExtractor:
    """依賴注入: 取得特徵提取器實例。"""
    global _feature_extractor
    if _feature_extractor is None:
        logger.info("Initializing SimSiam Feature Extractor...")
        # 將設定注入到 Extractor 中
        _feature_extractor = SimSiamFeatureExtractor(
            model_path=model_path,
            device=device
        )
    return _feature_extractor


def get_retrieval_service(
    repo: Annotated[VectorRepository, Depends(get_vector_repo)],
    extractor: Annotated[
        SimSiamFeatureExtractor, Depends(get_feature_extractor)
    ],
) -> RetrievalService:
    """依賴注入: 取得檢索服務實例。"""
    return RetrievalService(vector_repo=repo, feature_extractor=extractor)


# =============================================================================
# API Endpoints
# =============================================================================

# 建立允許的 MIME Type 常數集合 (Set 查詢時間複雜度為 O(1))
ALLOWED_ZIP_TYPES = {
    "application/zip",
    "application/x-zip-compressed",
    "multipart/x-zip"
}

ALLOWED_EXCEL_TYPES = {
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", # .xlsx
    "application/vnd.ms-excel" # .xls
}

@router.post(
    "/api/v1/dataset/upload",
    response_model=UploadResponse,
    status_code=202,
    summary="背景任務建立 (Upload & Start Task)",
    description="上傳工程圖壓縮檔與屬性明細表，啟動背景更新任務並回傳任務 ID。",
)
async def upload_dataset(
    background_tasks: BackgroundTasks,
    images_zip: Annotated[
        UploadFile, File(description="包含原始工程圖 PDF 檔案的嵌套 ZIP 壓縮檔")
    ],
    metadata_file: Annotated[
        UploadFile, File(description="圖檔明細表 (Excel)")
    ],
    debug: Annotated[
        bool, Form(description="是否開啟詳細 Debug 輸出，預設由環境變數決定")
    ] = os.getenv("DEBUG", "False").lower() in ("true", "1", "yes"),
    vector_repo: VectorRepository = Depends(get_vector_repo),
    feature_extractor: SimSiamFeatureExtractor = Depends(get_feature_extractor),
) -> UploadResponse:
    # 1. 修正：使用集合進行 MIME Type 的寬容驗證
    if images_zip.content_type not in ALLOWED_ZIP_TYPES:
        raise HTTPException(
            status_code=422, 
            detail=f"不支援的 ZIP 檔案格式。接收到的類型為: {images_zip.content_type}"
        )
        
    if metadata_file.content_type not in ALLOWED_EXCEL_TYPES:
        raise HTTPException(
            status_code=422, 
            detail=f"不支援的 Excel 檔案格式。接收到的類型為: {metadata_file.content_type}"
        )
    """接收上傳檔案並排程背景作業。"""

    # 讀取檔案二進位資料 (此處將消耗記憶體，若檔案極大建議分塊，不過 ZIP 通常需一次解開)
    # 若要進一步優化處理可先將 UploadFile 寫入磁碟。
    try:
        zip_bytes = await images_zip.read()
        meta_bytes = await metadata_file.read()
    except Exception as e:
        raise HTTPException(
            status_code=400, detail=f"讀取上傳檔案失敗: {str(e)}"
        )

    if not images_zip.filename.endswith(".zip"):
        raise HTTPException(
            status_code=400, detail="images_zip 必須為 ZIP 檔案。"
        )

    # 建立任務 ID
    task_id = f"req-{uuid.uuid4()}"

    # 排入背景任務執行 (BackgroundTasks)
    background_tasks.add_task(
        process_dataset_background,
        task_id=task_id,
        zip_bytes=zip_bytes,
        meta_bytes=meta_bytes,
        meta_filename=metadata_file.filename,
        vector_repo=vector_repo,
        feature_extractor=feature_extractor,
        debug=debug,
    )

    return UploadResponse(
        message="Dataset accepted. Indexing started in background.",
        task_id=task_id,
    )


@router.websocket("/ws/v1/update/{task_id}")
async def websocket_update(websocket: WebSocket, task_id: str):
    """
    WebSocket 端點：背景任務狀態推送。
    用於與前端建立連線，單向廣播非同步執行的進度。
    """
    await ws_manager.connect(task_id, websocket)
    try:
        # 保持連線，直到中斷或前端離線 (簡單接收迴圈)
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        ws_manager.disconnect(task_id)
        logger.info(f"WebSocket client disconnected for task: {task_id}")

@router.websocket("/ws/v1/update")
async def websocket_update_fallback(websocket: WebSocket, task_id: str | None = None):
    """
    容錯路由：接住前端舊版快取、Nginx Health Check 或漏傳 task_id 的連線。
    先明確 accept() 再優雅關閉，防止 Uvicorn 拋出 HTTP 403 Forbidden 並洗版終端機。
    """
    if task_id is None:
        task_id = websocket.query_params.get("task_id")
        
    if not task_id:
        await websocket.accept()
        await websocket.close(code=1000, reason="Missing task_id")
        return

    await ws_manager.connect(task_id, websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        ws_manager.disconnect(task_id)
        logger.info(f"WebSocket fallback client disconnected for task: {task_id}")


@router.post(
    "/api/v1/retrieval",
    response_model=RetrievalResponse,
    summary="影像檢索 (Image Retrieval)",
    description="接收前端上傳的 PDF 檔案與檢索條件，觸發完整的工程圖檢索管線，回傳比對結果。",
)
async def retrieval(
    file: Annotated[
        UploadFile, File(description="要檢索的工程圖 PDF 檔案 (最大 100MB)")
    ],
    k: Annotated[
        int, Form(ge=1, le=50, description="預期回傳的 Top K 相似圖片數量")
    ] = 10,
    conds: Annotated[
        str | None, Form(description="篩選條件的 JSON 字串化")
    ] = None,
    service: RetrievalService = Depends(get_retrieval_service),
) -> RetrievalResponse:
    """處理影像實時檢索請求。"""
    if file.content_type != "application/pdf":
        raise HTTPException(
            status_code=400, detail="僅支援 PDF 檔案格式 (application/pdf)。"
        )

    # 讀取並驗證大小 (粗略限制，實際可做中介層防護)
    # 這裡直接讀進記憶體
    pdf_bytes = await file.read()
    if len(pdf_bytes) > 100 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="檔案過大，限制為 100MB。")

    # 解析條件字串
    conditions_obj = RetrievalConditions()
    if conds:
        try:
            conds_dict = json.loads(conds)
            conditions_obj = RetrievalConditions(**conds_dict)
        except Exception as e:
            raise HTTPException(
                status_code=400, detail=f"無效的 conds JSON 格式: {str(e)}"
            )

    task_id = f"uuid-string-{uuid.uuid4()}"

    try:
        process_time, manifest = await service.search(
            task_id=task_id,
            pdf_bytes=pdf_bytes,
            top_k=k,
            conditions=conditions_obj,
        )
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    except Exception as e:
        logger.error(f"檢索服務發生未預期的錯誤: {e}")
        raise HTTPException(status_code=500, detail="內部伺服器錯誤")

    return RetrievalResponse(
        task_id=task_id, process_time_sec=process_time, manifest=manifest
    )


@router.get(
    "/api/v1/categories",
    response_model=CategoryListResponse,
    summary="取得所有工程圖類別 (Categories)",
    description="從資料庫中聚合現有所有的品名規格類別。",
)
async def get_categories(
    repo: VectorRepository = Depends(get_vector_repo),
) -> CategoryListResponse:
    categories = repo.get_all_categories()
    return CategoryListResponse(categories=categories)
