"""
定義 API 系統中 Request、Response 與 WebSocket 溝通的資料模型 (DTOs)。
遵循 Pydantic 模型驗證機制。
"""

from pydantic import BaseModel, Field

# =============================================================================
# REST API 請求與回應 Models
# =============================================================================


class UploadResponse(BaseModel):
    """資料集上傳成功回應"""

    status: str = Field(default="success", description="狀態，通常為 success")
    message: str = Field(..., description="回應訊息")
    task_id: str = Field(..., description="生成的背景任務 ID")


class CategoryListResponse(BaseModel):
    """工程圖類別列表回應"""

    status: str = Field(default="success", description="狀態，通常為 success")
    categories: list[str] = Field(
        default_factory=list, description="類別字串列表"
    )


class RetrievalConditions(BaseModel):
    """
    檢索條件模型。
    對應 `conds` JSON 字串解析後的結構。
    """

    type: str | None = Field(None, description="工程圖類別(品名規格)過濾")
    part_number: str | None = Field(None, description="品號模糊搜尋或精確比對")
    similarity: list[float] | None = Field(
        None, description="相似度閾值區間 [min, max]"
    )
    startDate: str | None = Field(
        None, description="最後交易日過濾起點，例如 2023/01/01"
    )
    endDate: str | None = Field(
        None, description="最後交易日過濾終點，例如 2023/12/31"
    )


class RetrievalManifestItem(BaseModel):
    """單一檢索結果項目"""

    id: str = Field(..., description="圖片 Hash 識別碼")
    name: str = Field(..., description="原始圖檔名稱")
    path: str = Field(..., description="前端可存取的靜態圖片路徑")
    type: str | None = Field(None, description="工程圖類別(品名規格)")
    part_number: str | None = Field(None, description="品號")
    version: str | None = Field(None, description="番數")
    transaction_date: str | None = Field(
        None, description="最後交易日/最後異動日"
    )
    standard_cost: float | None = Field(None, description="標準成本")
    similarity: float = Field(..., description="綜合相似度分數")


class RetrievalResponse(BaseModel):
    """影像檢索成功回應"""

    status: str = Field(default="success", description="狀態")
    task_id: str = Field(..., description="此次檢索的追蹤 ID")
    process_time_sec: float = Field(..., description="處理耗時(秒)")
    manifest: list[RetrievalManifestItem] = Field(
        default_factory=list, description="檢索結果列表"
    )


class ErrorResponse(BaseModel):
    """系統錯誤回應"""

    status: str = Field(default="error", description="狀態，固定為 error")
    message: str = Field(..., description="錯誤描述")


# =============================================================================
# WebSocket 推播 Models
# =============================================================================


class ProgressMessage(BaseModel):
    """
    WebSocket 進度推播訊息結構。
    用於向前端回報非同步背景任務的執行狀態。
    """

    task_id: str = Field(..., description="任務 ID")
    step: int = Field(..., description="階段編號 (1~7)")
    step_name: str = Field(..., description="階段名稱")
    status: str = Field(..., description="狀態：processing, success, error")
    progress_percent: int = Field(
        ..., ge=0, le=100, description="進度百分比 (0-100)"
    )
    message: str = Field(..., description="詳細訊息或進度說明")
    timestamp: str = Field(..., description="ISO 8601 格式時間戳記")
