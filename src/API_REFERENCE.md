# 工程圖影像檢索系統 - API Reference

## 1. 系統架構簡述

本系統基於 **FastAPI** 構建，透過三層式架構設計 (Router, Service, Repository) 封裝業務邏輯與資料庫互動。
底層檢索引擎使用 **SimSiam 自監督學習模型** 提取圖像特徵，並透過 **ChromaDB 向量資料庫** 進行高維空間之 KNN 相似度搜尋。
在多 ROI 或多分頁的特徵分數聚合上，採用 **MaxPooling 策略** (取最高分) 以確保完全吻合 (Exact Match) 的檢索精度。

系統採用混合式架構處理不同情境的需求：

- **背景非同步處理機制**：透過 `BackgroundTasks` 與 `WebSocket` 實作上傳海量資料庫的耗時作業，提供即時防阻塞的進度推播。
- **即時檢索管線**：以實時 HTTP Request 回應 PDF 影像的 ROI 高精度計算比對，並結合 Pydantic 進行輸入輸出驗證。

---

## 2. REST API 說明

### 2.1 背景任務建立 (Upload & Start Task)

接收前端上傳的壓縮檔與明細表，建立背景更新任務並回傳任務 ID。

- **Endpoint**: `POST /api/v1/dataset/upload`
- **Content-Type**: `multipart/form-data`
- **Request Parameters**:

| 欄位名稱 | 型別 | 必填 | 說明 |
| --- | --- | --- | --- |
| `images_zip` | File | 是 | 包含原始工程圖 PDF 檔案的嵌套 ZIP 壓縮檔 |
| `metadata_file` | File | 是 | 圖檔明細表 (Excel 或 CSV 格式) |
| `debug` | Boolean | 否 | 是否開啟詳細 Debug 輸出，預設由環境變數決定 (`True`/`False`) |

- **Responses**:
  - `202 Accepted`

  ```json
  {
      "status": "success",
      "message": "Dataset accepted. Indexing started in background.",
      "task_id": "req-9b1deb4d-3b7d-4bad-9bdd-2b0d7b3dcb6d"
  }
  ```

### 2.2 影像檢索 (Image Retrieval)

接收前端上傳的 PDF 檔案與檢索條件，觸發檢索管線，回傳 Top K 比對結果。

- **Endpoint**: `POST /api/v1/retrieval`
- **Content-Type**: `multipart/form-data`
- **Request Parameters**:

| 欄位名稱 | 型別 | 必填 | 說明 |
| --- | --- | --- | --- |
| `file` | File | 是 | 要檢索的工程圖 PDF 檔案 (最大限制 100MB)。 |
| `k` | Integer | 否 | 預期回傳的 Top K 相似圖片數量 (預設：10，範圍：1~50)。 |
| `conds` | String (JSON) | 否 | 篩選條件的 JSON 字串化 (Stringified JSON)。 |

- **`conds` JSON 解析結構**:

  ```json
  {
      "type": "立柱界面",               // 工程圖類別(品名規格)過濾
      "part_number": "YJUCVC0A...",   // 品號比對
      "similarity": [0.5, 1.0],       // 相似度閾值區間
      "startDate": "2023/01/01",      // 最後交易日過濾起點 (格式: YYYY/MM/DD)
      "endDate": "2023/12/31"         // 最後交易日過濾終點 (格式: YYYY/MM/DD)
  }
  ```

- **Responses**:
  - `200 OK`
  
  ```json
  {
      "status": "success",
      "task_id": "uuid-string-1234",
      "process_time_sec": 4.25,
      "manifest": [
          {
              "id": "hash_name",
              "name": "original_name.pdf",
              "path": "/static/images/hash_name.png",
              "type": "立柱界面",
              "part_number": "YJUCVC0A-50032180100",
              "version": "50",
              "transaction_date": "2023/09/21",
              "standard_cost": 138505.0,
              "similarity": 0.965
          }
      ]
  }
  ```

### 2.3 取得類別列表API

從資料庫中取得已經存在的工程圖類別「品名規格」列表。

- **Endpoint**: `GET /api/v1/categories`
- **Responses**:
  - `200 OK`

  ```json
  {
      "status": "success",
      "categories": [
          "刀庫底座", "立柱界面", "固定夾塊"
      ]
  }
  ```

### 2.4 前端與靜態目錄 (Frontend & Static Files)

本系統於 `main.py` 內建了基本的前端互動測試介面與靜態檔案服務。

- **前端互動測試頁面**:
  - **Endpoint**: `GET /`
  - **說明**: 回傳 Jinja2 渲染之 `index.html`，作為 WebSocket 與 API 測試用之前端介面。
  
- **靜態圖片資源**:
  - **Endpoint**: `GET /static/images/{hash_name}.png`
  - **說明**: 系統於背景任務執行時，會將圖檔放置於本地 `static/images/` 目錄下。前端可依據 `manifest` 回傳列表中的 `path` 欄位下載展示該圖片。

### 2.5 API 錯誤回應 (Error Responses)

當 API 觸發異常 (或規格不符) 時，FastAPI 會拋出對應的 `HTTPException` 並回傳預設的 JSON 錯誤格式，或由 `ErrorResponse` 定義之格式：

```json
{
    "detail": "專屬的錯誤敘述 (例如：無效的 conds JSON 格式)"
}
```

常見狀態碼列表：

- **`400 Bad Request`**: 參數格式錯誤、上傳檔案非指定格式 (例如副檔名不是 `.zip` 或是非 `application/pdf`)、JSON 條件字串無法解析等。
- **`413 Payload Too Large`**: 上傳的檔案容量超出限制 (例如 `retrieval` 上傳檔案 `file` 限制最大 100MB)。
- **`500 Internal Server Error`**: 伺服器內部發生非預期的例外錯誤，例如資料庫連線失敗等。

---

## 3. WebSocket 事件通訊協定 (重點)

前端發起 `POST /api/v1/dataset/upload` 後會獲得 `task_id`，隨即以該 ID 建立 WebSocket 連線接收後端單向推播的進度資訊。

- **Endpoints**:
  - `ws://<host>/ws/v1/update/{task_id}` (主要工作連線)
  - `ws://<host>/ws/v1/update?task_id={task_id}` (舊版或代理伺服器的 Fallback 路由容錯機制)
- **通訊方向**: 伺服器 (Server) 單向推播至 客戶端 (Client)
- **訊息格式**: JSON 字串

### 3.1 執行階段定義 (Steps)

整個非同步建檔操作分為 7 個循序階段：

1. **接收與初始化**: 準備作業環境與暫存目錄。
2. **解壓縮與掃描嵌套結構**: 解開 ZIP 並遍歷找出所有 PDF 檔案，將其轉換為 PNG 影像。
3. **複製與 Hash 重新命名**: 計算 Hash、複製影像檔案至靜態資源目錄以利取用。
4. **解析 Metadata 屬性資料並配對**: 從 Excel/CSV 提取屬性並對應 Hash 圖片。
5. **模型預測與 ROI 處理**: 切割 ROI 並驅動神經網路運算特徵提取。
6. **更新 ChromaDB**: 寫入/更新高維度向量與 Metadata 至資料庫叢集。
7. **完成與資源清理**: 刪除 `/tmp` 暫存，回報完成。

### 3.2 標準推播格式 (ProgressMessage)

```json
{
    "task_id": "req-9b1deb4d-3b7d-4bad-9bdd-2b0d7b3dcb6d",
    "step": 2,
    "step_name": "解壓縮與掃描嵌套結構",
    "status": "processing",  // 狀態列舉: processing, success, error
    "progress_percent": 15,  // 目前累積進度 (0~100)
    "message": "正在解壓縮 ZIP 檔案並掃描目錄...",
    "timestamp": "2026-02-24T17:05:31Z"
}
```

### 3.3 錯誤異常推播 (Error Handling)

若過程發生無法復原之錯誤，將觸發 Rollback 機制，並推播最後的錯誤訊息，隨後關閉連線：

```json
{
    "task_id": "req-9b1deb4d...dcb6d",
    "step": 6,
    "step_name": "發生錯誤",
    "status": "error",
    "progress_percent": 100,
    "message": "處理中發生錯誤：連接 ChromaDB 失敗：Connection Timeout",
    "timestamp": "2026-02-24T17:09:00Z"
}
```

前端接收到 `status: error` 應中斷連線並提供重試選項。

---

## 4. 環境變數配置 (Environment Variables)

本系統支援透過環境變數進行全局設定，或由 `src/demo/main.py` 中的 `APP_CONFIG` 定義預設值：

| 環境變數名稱 | 預設值 | 說明 |
| --- | --- | --- |
| `DB_PATH` | `./chroma_db` | ChromaDB 向量資料庫儲存路徑 |
| `COLLECTION_NAME` | `engineering_drawings` | ChromaDB 的 Collection 名稱 |
| `MODEL_PATH` | `./model/checkpoint_best.pth` | SimSiam 模型權重檔案路徑 |
| `DEVICE` | `cuda` | 模型推論使用的硬體裝置 (`cuda` 或 `cpu`) |
| `DEBUG` | `False` | 是否開啟詳細的除錯日誌輸出 (`True`/`False`) |
