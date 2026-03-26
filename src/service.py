"""
業務邏輯層 (Service Layer)。
負責處理系統核心業務邏輯：
1. 背景索引建置任務 (解壓縮、Hash化、擷取 Metadata 與 ROI特徵、寫入 DB)。
2. WebSocket 進度推播。
3. PDF 轉換與實時影像檢索。
"""

import asyncio
import datetime
import hashlib
import io
import logging
import shutil
import zipfile
from collections import defaultdict
from pathlib import Path
from typing import Any

import cv2
import fitz  # PyMuPDF
import numpy as np
from fastapi import WebSocket
from src.repo import MetadataRepository, VectorRepository
from src.schemas import (
    ProgressMessage,
    RetrievalConditions,
    RetrievalManifestItem,
)
from src.vector_search.engine import RetrievalEngine, WeightedSumStrategy, MaxPoolingStrategy
from src.vector_search.feature_extractor import SimSiamFeatureExtractor
from src.vector_search.utils import extract_rois_from_image

logger = logging.getLogger(__name__)


# 全域管理物件
class ConnectionManager:
    """管理 WebSocket 連線集合以支援進度推播。"""

    def __init__(self):
        # task_id -> WebSocket
        self.active_connections: dict[str, WebSocket] = {}
        # task_id -> asyncio.Event (用於同步等待前端連線)
        self.task_events: dict[str, asyncio.Event] = {}

    async def connect(self, task_id: str, websocket: WebSocket):
        await websocket.accept()
        self.active_connections[task_id] = websocket
        logger.info(f"WebSocket 建立連線: {task_id}")
        # 觸發等待的背景任務開始執行
        if task_id in self.task_events:
            self.task_events[task_id].set()

    def disconnect(self, task_id: str):
        if task_id in self.active_connections:
            del self.active_connections[task_id]
            logger.info(f"WebSocket 中斷連線: {task_id}")
        if task_id in self.task_events:
            del self.task_events[task_id]

    async def send_progress(self, message: ProgressMessage):
        task_id = message.task_id
        if task_id in self.active_connections:
            try:
                await self.active_connections[task_id].send_text(
                    message.model_dump_json()
                )
            except Exception as e:
                logger.error(f"WebSocket 推播失敗 ({task_id}): {e}")
                self.disconnect(task_id)

    async def wait_for_connection(self, task_id: str, timeout: int = 30) -> bool:
        """
        讓背景任務等待前端建立連線後才開始執行。
        如果在 timeout 秒內未連線，回傳 False。
        """
        if task_id not in self.task_events:
            self.task_events[task_id] = asyncio.Event()
            
        # 如果在此之前已經連線 (通常不會，但保險起見)
        if task_id in self.active_connections:
            self.task_events[task_id].set()
            return True

        try:
            await asyncio.wait_for(self.task_events[task_id].wait(), timeout=timeout)
            return True
        except asyncio.TimeoutError:
            logger.error(f"Task {task_id} 等待 WebSocket 連線逾時 ({timeout}s)")
            return False


ws_manager = ConnectionManager()

# =============================================================================
# Background Indexing Service
# =============================================================================


async def process_dataset_background(
    task_id: str,
    zip_bytes: bytes,
    meta_bytes: bytes,
    meta_filename: str,
    vector_repo: VectorRepository,
    feature_extractor: SimSiamFeatureExtractor,
    debug: bool = False,
):
    """
    背景非同步更新流程。

    階段：
    1. 接收與初始化
    2. 解壓縮與掃描嵌套結構
    3. 複製與 Hash 重新命名
    4. 解析 Metadata 屬性資料並配對
    5. 模型預測與 ROI 處理
    6. 更新 ChromaDB
    7. 完成與資源清理
    """
    tmp_dir = Path(f"/tmp/indexing_{task_id}")
    static_images_dir = Path("static/images")
    static_images_dir.mkdir(parents=True, exist_ok=True)

    current_step_num = 1
    current_step_name = "接收連線與初始化"

    def debug_log(msg: str):
        if debug:
            logger.info(f"[DEBUG Task {task_id}] {msg}")

    async def send_step(step: int, name: str, status: str, percent: int, msg: str):
        nonlocal current_step_num, current_step_name
        current_step_num = step
        current_step_name = name
        p_msg = ProgressMessage(
            task_id=task_id,
            step=step,
            step_name=name,
            status=status,
            progress_percent=percent,
            message=msg,
            timestamp=datetime.datetime.utcnow().isoformat() + "Z",
        )
        await ws_manager.send_progress(p_msg)
        await asyncio.sleep(0)  # Yield to event loop to ensure message is sent

    # 在所有作業開始之前，先等待前端 WebSocket 過來敲門報到
    logger.info(f"Task {task_id} 正在等待前端 WebSocket 連線...")
    connected = await ws_manager.wait_for_connection(task_id, timeout=30)
    if not connected:
        logger.error(f"Task {task_id} 因等不到 WebSocket 連線，已中止背景任務。")
        return

    try:
        # Step 1: 初始化
        await send_step(
            1, "接收與初始化", "processing", 5, "開始初始化任務作業環境..."
        )

        if tmp_dir.exists():
            shutil.rmtree(tmp_dir)
        tmp_dir.mkdir(parents=True, exist_ok=True)

        # Step 2: 解壓縮圖片與處理嵌套結構
        await send_step(
            2,
            "解壓縮與掃描嵌套結構",
            "processing",
            10,
            "正在解壓縮 ZIP 檔案並掃描目錄...",
        )
        with zipfile.ZipFile(io.BytesIO(zip_bytes), "r") as zip_ref:
            for member in zip_ref.namelist():
                if (
                    member.endswith("/")
                    or "__MACOSX" in member
                    or ".DS_Store" in member
                ):
                    continue
                if not member.lower().endswith(".pdf"):
                    continue
                member_path = Path(member)
                target_path = tmp_dir / member_path.name
                debug_log(f"解壓縮檔案: {member}")
                try:
                    with (
                        zip_ref.open(member) as source,
                        open(target_path, "wb") as target,
                    ):
                        shutil.copyfileobj(source, target)
                    await asyncio.sleep(0)  # Yield to event loop
                except Exception as e:
                    logger.warning(f"跳過損壞的檔案 {member}: {e}")
                    continue

        all_pdfs = list(tmp_dir.glob("*.pdf"))
        total_pdfs = len(all_pdfs)
        if total_pdfs == 0:
            raise ValueError("ZIP 檔案中未發現有效支援的 PDF 檔案。")

        await send_step(
            2,
            "解壓縮與掃描嵌套結構",
            "processing",
            18,
            f"解壓縮完成，共 {total_pdfs} 份 PDF。正在進行影像轉檔...",
        )
        
        all_images = []
        dpi = 100
        scale = dpi / 72.0
        
        for idx, pdf_path in enumerate(all_pdfs):
            try:
                doc = fitz.open(pdf_path)
                if doc.page_count > 0:
                    page = doc.load_page(0)
                    pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
                    png_path = pdf_path.with_suffix(".png")
                    pix.save(png_path.as_posix())
                    all_images.append(png_path)
                doc.close()
                await asyncio.sleep(0)  # Yield to event loop
            except Exception as e:
                logger.error(f"轉換 {pdf_path.name} 失敗: {e}")
                
        total_imgs = len(all_images)
        if total_imgs == 0:
            raise ValueError("所有 PDF 轉換影像失敗，無有效影像。")

        await send_step(
            2,
            "解壓縮與掃描嵌套結構",
            "success",
            28,
            f"轉換完成，獲得 {total_imgs} 張工程圖影像。",
        )

        # Step 3: 複製與 Hash 重新命名
        await send_step(
            3,
            "複製與 Hash 重新命名",
            "processing",
            30,
            "正在計算 Hash 並移轉檔案至靜態目錄...",
        )

        image_records = []
        for idx, img_path in enumerate(all_images):
            file_content = img_path.read_bytes()
            img_hash = hashlib.sha256(file_content).hexdigest()
            dest_path = static_images_dir / f"{img_hash}.png"

            if not dest_path.exists():
                shutil.copy(img_path, dest_path)
            
            if img_path.exists():
                img_path.unlink()

            image_records.append(
                {
                    "original_filename": img_path.name,
                    "original_stem": img_path.stem,
                    "img_hash": img_hash,
                    "dest_path": dest_path,
                    "metadata": {},
                }
            )

            if idx % max(1, total_imgs // 10) == 0:
                p = 30 + int(idx / total_imgs * 10)
                await send_step(
                    3,
                    "複製與 Hash 重新命名",
                    "processing",
                    p,
                    f"已處理 {idx}/{total_imgs} 個檔案...",
                )
            
            await asyncio.sleep(0)  # Yield to event loop

        await send_step(3, "複製與 Hash 重新命名", "success", 40, "檔案重新命名並展平完成。")

        # Step 4: 解析 Metadata 屬性資料並進行檔名配對
        await send_step(4, "解析 Metadata 屬性資料並配對", "processing", 42, "解析明細表檔案結構並進行檔名配對...")
        metadata_map = MetadataRepository.parse_metadata_file(
            meta_bytes, meta_filename
        )
        debug_log(f"解析獲得 {len(metadata_map)} 筆 Metadata 紀錄")
        
        for idx, rec in enumerate(image_records):
            original_stem = rec["original_stem"]
            matched_meta = {}
            for k, v in metadata_map.items():
                if k in original_stem or original_stem in k:
                    matched_meta = v
                    break
            rec["metadata"] = matched_meta
            debug_log(f"處理圖檔: {rec['original_filename']}, Hash: {rec['img_hash']}, 是否配對屬性: {'是' if matched_meta else '否'}")
            if idx % max(1, len(image_records) // 10) == 0:
                await asyncio.sleep(0)

        await send_step(4, "解析 Metadata 屬性資料並配對", "success", 50, "Metadata 屬性配對完成。")

        # Step 5: 模型運算 (預處理與 Embedding 提取)
        await send_step(
            5,
            "模型預測與 ROI 處理",
            "processing",
            55,
            "開始進行圖片 ROI 分割與特徵向量計算...",
        )

        all_vectors = []
        all_metadatas = []
        all_ids = []
        total_records = len(image_records)

        for idx, rec in enumerate(image_records):
            dest_path = rec["dest_path"]
            img_hash = rec["img_hash"]
            meta = rec["metadata"]
            orig_name = rec["original_filename"]

            try:
                img = cv2.imdecode(
                    np.fromfile(str(dest_path), dtype=np.uint8),
                    cv2.IMREAD_COLOR,
                )
                if img is None:
                    continue
                    
                if len(img.shape) == 2:
                    img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
                elif len(img.shape) == 3 and img.shape[2] == 1:
                    img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
                elif len(img.shape) == 3 and img.shape[2] == 4:
                    img = cv2.cvtColor(img, cv2.COLOR_BGRA2BGR)

                components = extract_rois_from_image(img, top_n=5)
                debug_log(f"擷取影像 {orig_name} ROI，共得到 {len(components) if components else 0} 個元件")
                if not components:
                    components = [
                        (
                            img,
                            {
                                "component_index": 0,
                                "bbox": [0, 0, img.shape[1], img.shape[0]],
                                "area": img.shape[0] * img.shape[1],
                            },
                        )
                    ]

                for roi_img, info in components:
                    comp_idx = info["component_index"]

                    try:
                        emb = feature_extractor.extract_batch([roi_img])[0]
                    except Exception as e:
                        logger.warning(
                            f"Feature extraction failed for {img_hash} ROI {comp_idx}: {e}"
                        )
                        continue

                    sheet_name = str(meta.get("sheet_name") or "").strip()
                    spec_name = str(meta.get("品名規格") or "").strip()
                    
                    # 優先使用分頁名稱，若無分頁名稱或為空值，則使用原本屬性表中的品名規格
                    final_type = sheet_name if sheet_name else spec_name
                    if not final_type or final_type == "None":
                        final_type = "未分類"

                    c_meta = {
                        "original_filename": orig_name,
                        "page_num": 1,
                        "component_type": f"roi_{comp_idx}",
                        "parent_pdf_id": img_hash,
                        "type": final_type,
                        "part_number": str(meta.get("品號") or ""),
                        "version": str(meta.get("番數") or ""),
                        "transaction_date": str(meta.get("最後交易日/最後異動日") or ""),
                        "transaction_date_int": int(meta.get("transaction_date_int") or 0),
                        "standard_cost": float(meta.get("標準成本") or 0.0),
                    }

                    all_vectors.append(emb.tolist())
                    all_metadatas.append(c_meta)
                    all_ids.append(f"{img_hash}_roi_{comp_idx}")

            except Exception as e:
                logger.error(f"Image ROI processing error {orig_name}: {e}")

            if idx % max(1, total_records // 10) == 0:
                p = 55 + int(idx / total_records * 20)
                await send_step(
                    5,
                    "模型預測與 ROI 處理",
                    "processing",
                    p,
                    f"特徵向量計算中 ({idx}/{total_records})...",
                )
            await asyncio.sleep(0)

        await send_step(5, "模型預測與 ROI 處理", "success", 75, f"特徵向量計算完成，共 {len(all_ids)} 個 ROI。")

        # Step 6: Update 向量資料庫
        await send_step(
            6,
            "更新 ChromaDB",
            "processing",
            75,
            "開始寫入向量資料庫...",
        )
        
        batch_size = 32
        success_upsert_count = 0
        total_vectors = len(all_ids)
        for batch_start in range(0, total_vectors, batch_size):
            end = batch_start + batch_size
            vector_repo.upsert_vectors(
                all_vectors[batch_start:end], 
                all_metadatas[batch_start:end], 
                all_ids[batch_start:end]
            )
            success_upsert_count += len(all_ids[batch_start:end])
            p = 75 + int(batch_start / max(1, total_vectors) * 20)
            await send_step(
                6, 
                "更新 ChromaDB", 
                "processing", 
                p, 
                f"正在寫入資料庫 ({success_upsert_count}/{total_vectors})..."
            )
            await asyncio.sleep(0)  # Yield to event loop

        await send_step(6, "更新 ChromaDB", "success", 95, "資料庫寫入完成。")

        # Step 7: 任務完成與資源清理
        await send_step(
            7, "完成與資源清理", "processing", 95, "正在清理暫存資料..."
        )
        shutil.rmtree(tmp_dir, ignore_errors=True)

        await send_step(
            7,
            "完成與資源清理",
            "success",
            100,
            f"任務完成！共成功建立 {success_upsert_count} 個向量。",
        )

    except Exception as e:
        logger.error(f"任務處理發生例外錯誤: {e}")
        # 異常回滾機制 (Rollback)
        shutil.rmtree(tmp_dir, ignore_errors=True)
        await send_step(current_step_num, current_step_name, "error", 100, f"處理中發生錯誤：{e}")


# =============================================================================
# Retrieval Service
# =============================================================================


class RetrievalService:
    """提供影像檢索的具體實作。"""

    def __init__(
        self,
        vector_repo: VectorRepository,
        feature_extractor: SimSiamFeatureExtractor,
    ):
        """初始化檢索服務。"""
        self.vector_repo = vector_repo
        self.feature_extractor = feature_extractor

        # 使用自帶的 RetrievalEngine 並配置預設的 MaxPoolingStrategy 以利 Exact Match 檢索
        self.engine = RetrievalEngine(
            db_manager=self.vector_repo.db,
            feature_extractor=self.feature_extractor,
            aggregation_strategy=MaxPoolingStrategy(),
        )

    async def _pdf_to_images(self, pdf_bytes: bytes) -> list[np.ndarray]:
        """將上傳的 PDF 檔案轉換為 CV2 影像格式。預設 100 DPI。"""
        images = []
        try:
            doc = fitz.open(stream=pdf_bytes, filetype="pdf")
            for page in doc:
                # 縮放至 100 DPI (72 DPI is matrix 1.0, 100/72 ≈ 1.38)
                matrix = fitz.Matrix(1.38, 1.38)
                pix = page.get_pixmap(matrix=matrix)

                # Convert fitz pixmap to numpy array -> cv2 BGR format
                img_data = np.frombuffer(pix.samples, dtype=np.uint8)
                img = img_data.reshape(pix.height, pix.width, pix.n)

                if pix.n == 4:  # RGBA
                    img = cv2.cvtColor(img, cv2.COLOR_RGBA2BGR)
                elif pix.n == 3:  # RGB
                    img = cv2.cvtColor(img, cv2.COLOR_RGB2BGR)
                elif pix.n == 1:  # GRAY
                    img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)

                images.append(img)
            return images
        except Exception as e:
            logger.error(f"PDF 轉換影像失敗: {e}")
            raise RuntimeError(f"PDF 轉換失敗: {e}")

    def _build_chroma_filter(
        self, conds: RetrievalConditions
    ) -> dict[str, Any] | None:
        """建立符合 ChromaDB 語法的過濾條件支援查詢。"""
        filters = []

        if conds.type:
            filters.append({"type": {"$eq": conds.type}})

        # ChromaDB Where filter is limited (exact match or simple operations), simple implementation:
        if conds.part_number:
            # Note: ChromaDB doesn't natively support substring search well in `where`,
            # usually relies on string exact match. We implement an exact match for simple query.
            filters.append({"part_number": {"$eq": conds.part_number}})

        # Date Filtering (Use transaction_date_int for int range queries)
        def parse_date_to_int(d_str: str) -> int:
            """轉換如 2026/3/2 或 2026-03-02 為 20260302"""
            cleaned = d_str.replace("-", "/").strip()
            parts = cleaned.split("/")
            if len(parts) == 3:
                y, m, d = parts
                return int(f"{y}{int(m):02d}{int(d):02d}")
            return int(cleaned.replace("/", ""))

        if conds.startDate or conds.endDate:
            if conds.startDate:
                start_int = parse_date_to_int(str(conds.startDate))
                filters.append({"transaction_date_int": {"$gte": start_int}})
            if conds.endDate:
                end_int = parse_date_to_int(str(conds.endDate))
                filters.append({"transaction_date_int": {"$lte": end_int}})
                # 避免將無日期 (0) 的圖檔錯誤放行
                filters.append({"transaction_date_int": {"$gt": 0}})

        if not filters:
            return None
        elif len(filters) == 1:
            return filters[0]
        else:
            return {"$and": filters}

    def transform_similarity_power(self, similarity: float, gamma: float = 3.0) -> float:
        """
        使用指數變換來重新分佈相似度分數。
        
        Args:
            similarity (float): 原始相似度，範圍 [0.0, 1.0]。
            gamma (float): 指數參數，大於 1.0 以壓縮低分並拉開高分區間。
            
        Returns:
            float: 轉換後的相似度，範圍 [0.0, 1.0]。
        """
        # 確保數值在合理的定義域內，避免異常輸入
        clamped_sim = max(0.0, min(1.0, similarity))
        return clamped_sim ** gamma

    async def search(
        self,
        task_id: str,
        pdf_bytes: bytes,
        top_k: int,
        conditions: RetrievalConditions,
    ) -> tuple[float, list[RetrievalManifestItem]]:
        """
        執行給定 PDF 的相似影像檢索流程。

        Args:
            task_id: 任務標識符。
            pdf_bytes: PDF 的二進位資料。
            top_k: 請求取得的相似 Top-K 結果。
            conditions: 元數據過濾條件集合。

        Returns:
            Tuple[float, List[RetrievalManifestItem]]: 返回處理時間、排序的 Manifest 結果。
        """
        start_time = datetime.datetime.now()

        # 1. PDF 轉多張 Images
        images = await self._pdf_to_images(pdf_bytes)
        if not images:
            raise ValueError("無法從 PDF 中解析出有效的影像。")

        # ChromaDB 條件過濾構建
        where_filter = self._build_chroma_filter(conditions)

        similarity_threshold_min = (
            conditions.similarity[0]
            if conditions.similarity and len(conditions.similarity) > 0
            else 0.0
        )
        similarity_threshold_max = (
            conditions.similarity[1]
            if conditions.similarity and len(conditions.similarity) > 1
            else 1.0
        )

        all_candidate_scores = defaultdict(float)
        all_candidate_details = {}

        # 2. 為每一頁 PDF 檢索相似結果
        # 使用 vector_search/engine.py 內的 Retrieval Engine 架構精神，略作改版以適應 Memory Image
        for i, img in enumerate(images):
            # i. ROI 分割
            components = extract_rois_from_image(img, top_n=5)
            if not components:
                roi_imgs = [img]
            else:
                roi_imgs = [roi[0] for roi in components]

            # ii. 特徵提取
            embeddings = self.feature_extractor.extract_batch(roi_imgs)
            if len(embeddings) == 0:
                continue

            # iii. KNN Query
            query_results = self.vector_repo.query_vectors(
                query_embeddings=embeddings.tolist(),
                n_results=top_k * 2,  # 查詢放大兩倍以利 後續 Grouping 與 Filter
                where_filter=where_filter,
            )

            # iv. 統計同一個 Hash Parent PDF 的加成與過濾
            page_candidates = defaultdict(
                lambda: {"scores": [], "metadatas": []}
            )
            num_components = len(embeddings)

            for j in range(num_components):
                batch_ids = query_results["ids"][j]
                batch_dists = query_results["distances"][j]
                batch_metas = query_results["metadatas"][j]

                for r_id, dist, meta in zip(
                    batch_ids, batch_dists, batch_metas
                ):
                    if not meta:
                        continue
                    parent_hash = meta.get("parent_pdf_id")
                    if not parent_hash:
                        continue

                    similarity = max(0.0, 1.0 - dist)
                    similarity = self.transform_similarity_power(similarity)
                    # Similarity Threshold Checking
                    # if (
                    #     similarity < similarity_threshold_min
                    #     or similarity > similarity_threshold_max
                    # ):
                    #     continue


                    page_candidates[parent_hash]["scores"].append(similarity)
                    page_candidates[parent_hash]["metadatas"].append(meta)

            # v. 分數聚合 Aggregation
            # 使用 MaxPoolingStrategy (取最高分)，避免 Exact Match 的 1.0 被其他較低分的次要特徵或雜訊稀釋為 0.8
            strategy = MaxPoolingStrategy()
            for parent_hash, data in page_candidates.items():
                final_score = strategy.aggregate(
                    data["scores"], data["metadatas"]
                )
                # 如果多頁 PDF 打中同一張圖片，我們取最佳分數或相加？通常取 Max
                if final_score > all_candidate_scores[parent_hash]:
                    all_candidate_scores[parent_hash] = final_score
                    # 隨機保留一個 matched metadata 以供最終顯示
                    all_candidate_details[parent_hash] = data["metadatas"][0]

        # 3. 生成 Manifest Result
        manifest: list[RetrievalManifestItem] = []

        # 排序
        sorted_candidates = sorted(
            all_candidate_scores.items(), key=lambda x: x[1], reverse=True
        )
        # 取 Top K
        for parent_hash, score in sorted_candidates[:top_k]:
            meta = all_candidate_details[parent_hash]

            # "/static/images/hash.png" 是由 FastAPI StaticFiles 提供服務的端點路徑
            img_path = f"/static/images/{parent_hash}.png"

            item = RetrievalManifestItem(
                id=parent_hash,
                name=meta.get("original_filename", "Unknown"),
                path=img_path,
                type=meta.get("type", None),
                part_number=meta.get("part_number", None),
                version=meta.get("version", None),
                transaction_date=meta.get("transaction_date", None),
                standard_cost=meta.get("standard_cost", None),
                similarity=round(score, 5),
            )
            manifest.append(item)

        end_time = datetime.datetime.now()
        process_time_sec = (end_time - start_time).total_seconds()

        return process_time_sec, manifest
