# 工程圖影像檢索系統 - Postman 測試指南

本指南將引導您使用 [Postman](https://www.postman.com/) 軟體來測試本系統的主要 Backend API，並包含如何使用 WebSocket 客戶端接收背景任務進度。

---

## 準備工作

1. 確保 FastAPI 伺服器正在運行中：預設網址通常為 `http://localhost:8000` 或 `http://localhost:8002` (請依照啟動時的 port 設定調整)。
2. 確認您手邊有一包包含工程圖檔案的 **ZIP 壓縮檔** (例如 `batch_2026_02.zip`)。
3. 確認您手邊有一份對應的 **屬性表明細** (`.xlsx` 或 `.csv`，例如 `metadata.xlsx`)。
4. 準備欲作為查詢條件使用的 **PDF 單獨工程圖** 檔案。

---

## 1. 資料庫建置與更新 API (HTTP POST)

此 API 用於上傳圖檔與明細表，建立後台索引計算任務。

### 設定步驟

1. **新增 Request**: 在 Postman 建立一個新的 Request，設定為 `POST`。
2. **URL**: 填入 `http://localhost:8002/api/v1/dataset/upload` (請將 Port 替換為實際號碼)。
3. **Body (重要)**:
   - 選擇 **`form-data`** 分頁。
   - 在 **Key** 欄位依序加入以下參數，並注意將右側下拉選單的 `Text` 切換為 `File` (針對檔案欄位)：
      - `images_zip` (File): 點選 `Select Files` 上傳您的 ZIP 壓縮檔。
      - `metadata_file` (File): 點選 `Select Files` 上傳您的 Excel 或 CSV 明細表。
      - `clear_existing` (Text): 填入 `true` 或 `false` (若要重置原本資料庫請輸入 true)。
      - `debug` (Text): 填入 `true` 或 `false` (選填)。
4. **Send**: 點擊 `Send` 送出請求。

### 預期結果

若成功，您會收到 `202 Accepted` 的回覆，回傳類似以下的 JSON：

```json
{
    "status": "success",
    "message": "Dataset accepted. Indexing started in background.",
    "task_id": "req-9b1deb4d-3b7d-4bad-9bdd-2b0d7b3dcb6d"
}
```

> **注意**: 記下拿到的 `task_id`，將在下一步 WebSocket 連接時使用。

---

## 2. 透過 WebSocket 接收進度

建立任務後，系統是在背景執行運算。需要透過 WebSocket 才能得知目前的進度與完成狀態。

### 設定步驟

1. **新建 WebSocket Request**:
   - 點擊 Postman 左上角的 `New` -> 選擇 `WebSocket`。
2. **URL**:
   - 填入 `ws://localhost:8002/ws/v1/update/{task_id}` (請替換為上一步拿到的 task_id，不用花括號)。
   - 例如: `ws://localhost:8002/ws/v1/update/req-9b1deb4d-3b7d-4bad-9bdd-2b0d7b3dcb6d`
3. **Connect**: 點擊右側的 `Connect` 按鈕。

### 預期結果

- 如果工作還在進行中，您會在下方的 `Messages` 視窗中，每當伺服器狀態更新時，即時看到包含 `step`、`status`、`progress_percent` 的 JSON 物件推播。
- 當收到 `"status": "success"` 且 `step: 7` 時，代表全部建置完畢。

---

## 3. 取得現有檢索類別列表 (HTTP GET)

測試取得資料庫中曾經建立的 "品名規格" 清單。

### 設定步驟

1. **新增 Request**: 設定為 `GET`。
2. **URL**: `http://localhost:8002/api/v1/categories`
3. **Send**: 點擊 `Send` 送出。

### 預期結果
你會得到 `200 OK`，格式如下：

```json
{
    "status": "success",
    "categories": [
        "ATC鈑金-(P)",
        "刀庫底座-(P)"
    ]
}
```

---

## 4. 工程圖影像檢索 API (HTTP POST)

測試上傳一張 PDF 來找尋資料庫中最相似的圖紙。

### 設定步驟

1. **新增 Request**: 設定為 `POST`。
2. **URL**: `http://localhost:8002/api/v1/retrieval`
3. **Body**:
   - 選擇 **`form-data`** 分頁。
   - 加入參數：
      - `file` (切換為 `File`): 上傳你要檢索的單獨工程圖 `.pdf` 檔。
      - `k` (Text, 選填): 填入數字，如 `5`，代表要找最相似的 5 張圖 (預設為 10)。
      - `conds` (Text, 選填): 取決於你要做的過濾條件。若不需過濾可留空。若要過濾，需輸入嚴格的 JSON 格式字串，例如：

        ```json
        {"type": "刀庫底座-(P)"}
        ```

        或者

        ```json
        {"part_number": "YJUCVC0A-50032180A0", "similarity": [0.6, 1.0]}
        ```

4. **Send**: 點擊 `Send` 送出請求。

### 預期結果
等待伺服器運算完成後，會收到 `200 OK` 的 JSON，包含您要的檢索結果：

```json
{
    "status": "success",
    "task_id": "uuid-string-1234",
    "process_time_sec": 3.45,
    "manifest": [
        {
            "id": "hash_name",
            "name": "original_filename.png",
            "path": "/static/images/hash_name.png",
            "type": "刀庫底座-(P)",
            "part_number": "YJUCVC0A-50032180A0",
            "version": "10",
            "transaction_date": "2024/02/01",
            "standard_cost": 500.0,
            "similarity": 0.985
        }
    ]
}
```
