# 02_Architecture_Insights.md

## 1. 架構模式 (Architecture Patterns)

當前 `src/demo` 採用了簡化的**分層式架構 (Layered Architecture)**，非常適合快速驗證概念 (PoC) 與初期開發，模組職責劃分如下：

- **表示與路由層 (Presentation/Routing Layer)**: `main.py` 負責定義對外的 API 端點 (`/`, `/ws`)、註冊中介軟體 (CORS) 以及模板 (Jinja2) 與靜態檔案的代理網址。
- **業務邏輯層 (Service Layer)**: `modules/retrieval/service.py` (`ProcessDrawingService`) 封裝了核心的系統排程，包含 PDF 暫存、格式轉換以及向量搜尋。
- **領域模型層 (Domain Layer)**: `modules/retrieval/models.py` 透過 Pydantic 定義所有進出業務邏輯層的標準資料結構，提升強型別檢查支援與序列化的便利性。
- **基礎設施層 (Infrastructure Layer)**: 實體的環境變數與路徑位於 `core/config.py`，而深度學習與 ChromaDB 向量搜尋的真正實作則委派至專案根目錄的外部 `vector_search` 模組，透過 `Router` (Facade 模式) 進行依賴注入。

該目錄結構 (`core`, `modules`) 邏輯清晰，易於依據不同領域建立新功能模組。其中 `get_service()` 中更實作了**單例模式 (Singleton Pattern)** 進行外部資源的**懶加載 (Lazy Loading)**，成功避免每次客戶端連線時重複初始化龐大且消耗記憶體的 `Router` 模型。

## 2. 安全性與效能評估 (Security & Performance)

### 潛在的安全性疑慮 (Security Risks)

- **CORS 設定過於寬鬆**: 目前 FastAPI 中設定了 `allow_origins=["*"]` 允許所有來源進行外部網站請求，這將大幅增加 CSRF 與被惡意第三方前台串接的風險。進入正式環境前需嚴格縮限至受信任的前端網域白名單。
- **缺乏身份驗證與授權 (Authentication/Authorization)**: 未見任何 JWT (JSON Web Token) 或 Session 攔截器，任何人皆可無限制地呼叫 WebSocket 接口，藉此頻繁觸發底層高耗能的 CPU/GPU 影響伺服器可用性 (DDoS)。
- **路徑防禦與惡意 Payload**: 暫存檔與生成檔名僅倚賴簡單的 `int(time.time())` 命名，在高並發環境下可能發生命名碰撞 (Name Collision)。此外，程式未限制上傳二進位資料的最大檔案容量限制，極可能導致記憶體溢位 (OOM)。

### 效能瓶頸評估 (Performance Bottlenecks)

- **非同步事件迴圈阻塞 (Event Loop Blocking)**: `_convert_pdf_to_images` 執行了 `fitz.open()`、大量的像素轉換 (NumPy) 與影像存儲 (`cv2.imwrite`)。這些全部都是 CPU 密集型與同步阻塞的磁碟 I/O，被直接寫死在 `async def` 路由內，這會導致 FastAPI 無法同一時間處理其他併發連線要求。
- **高昂的 Disk I/O 成本**: 系統在每次元件的分析上，都無謂地將圖片寫入實體硬碟 (`cv2.imwrite(str(temp_img_path), img)`)，再由 Router 讀取該路徑進行分析。這種頻繁往返磁碟的操作嚴重拖慢了微秒級別的 API 反應時間。
- **N+1 查詢與處理問題**:
  在拆分多頁工程圖時，程式碼為 `for i, img in enumerate(images): ... execute_image_search_by_path`，採取循序式呼叫 (Sequential Processing)。如果一份 PDF 有 100 頁，底層的 PyTorch/Transformer 模型將被迫啟動硬體環境 100 次，白白浪費了機器學習模型原生支援的**批次推理 (Batch Inference)** 效能優勢。

## 3. 現代化優化建議 (Modernization Best Practices)

為達企業級標準，提出以下三階段現代化升級建議：

1. **第一階段：I/O 優化與記憶體傳遞 (Memory Passing)**
   - 修改 `ProcessDrawingService` 與 Router 的協作方式：建議擴充或直接傳遞 `numpy` 陣列或 Bytes Buffer 給模型 (如實作 `execute_image_search_by_array`)，完全免去來回寫入暫存硬碟 SSD 帶來的無謂延遲，同時也解決暫存檔無法自動清理造成的雷區。

2. **第二階段：導入非同步任務列隊 (Asynchronous Task Queue)**
   - API Server (FastAPI) 應只負責受理請求並拋出任務。建議導入 **Celery**、**RQ** 或者先透過 FastAPI 內建的 `BackgroundTasks`/`ThreadPoolExecutor`，將耗時的「PDF 轉換」與「向量搜尋」拆交由背景預設的 Worker 節點執行。
   - 此時 WebSocket 僅需被視作「前端訂閱進度的推播頻道」(Pub/Sub，如接入 Redis Streams 架構)，實現請求端與運算端的徹底解耦 (Decoupling)。

3. **第三階段：批次吞吐量極大化與雲原生改造 (Cloud-Native & Batching)**
   - 將上傳的 PDF 從 Server 本地環境抽離，放至 S3 或 MinIO 等物件存儲中，支援容器水平擴展 (Horizontal Pod Autoscaling)。
   - **重構 Inference Layer**: 將單頁獨立呼叫的迴圈，改造為先把要轉換的 PDF 多頁影像推入一個佇列清單，組合成單一 Tensor Batch，並一次丟進 ML 模型推論，這可將整體 GPU 或 CPU 叢集的使用率與吞吐量拉升至原先架構的一倍以上。
