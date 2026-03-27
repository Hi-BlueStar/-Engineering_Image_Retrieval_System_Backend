"""
資料存取層 (Repository Layer)。
負責處理所有與底層資料儲存相關的操作，包含 ChromaDB 向量資料庫互動與 Excel/CSV 屬性表的解析。
遵循 Layer 3 原則，僅負責 I/O 與基本資料整理，不包含複雜業務邏輯。
"""

import io
import logging
from typing import Any

import pandas as pd
from src.vector_search.database import ChromaDBManager

logger = logging.getLogger(__name__)


class MetadataRepository:
    """
    Metadata 解析存取器。
    負責將使用者上傳的 Excel 或 CSV 解析為內部可用格式。
    """

    @staticmethod
    def parse_metadata_file(
        file_bytes: bytes, filename: str
    ) -> dict[str, dict[str, Any]]:
        """
        解析明細表，將每一張工程圖對應的屬性提取出來。

        Args:
            file_bytes (bytes): 檔案二進位內容。
            filename (str): 檔案名稱 (用以判斷副檔名)。

        Returns:
            Dict[str, Dict[str, Any]]: 檔名(或品號)為 Key，對應屬性字典為 Value 的映射表。
                                       依據需求，這裡回傳的 Key 可用於與解壓縮後的檔名做 Mapping。
        """
        logger.info(f"開始解析 Metadata 檔案: {filename}")
        try:
            if filename.endswith(".csv"):
                df = pd.read_csv(io.BytesIO(file_bytes))
                df["sheet_name"] = None
            elif filename.endswith((".xls", ".xlsx")):
                # 讀取 Excel 的所有分頁 (Sheet)，回傳 dict of DataFrames
                sheet_dict = pd.read_excel(io.BytesIO(file_bytes), sheet_name=None)
                for sheet_name, sheet_df in sheet_dict.items():
                    sheet_df["sheet_name"] = sheet_name
                # 將所有分頁的 DataFrame 合併為單一 DataFrame
                df = pd.concat(sheet_dict.values(), ignore_index=True)
            else:
                raise ValueError("不支援的檔案格式，請提供 Excel 或 CSV 檔案。")

            # 清理欄位名稱：移除欄位名稱中的所有空白與換行，解決類似 "品      號" 對不到 "品號" 的問題
            import re
            df.columns = [re.sub(r'\s+', '', str(col)).strip() for col in df.columns]

            # 尋找與交易日、異動日相關的所有欄位
            target_keywords = ["最後交易日", "最後異動日", "交易日", "異動日", "異動日期"]
            date_cols = [col for col in df.columns if any(keyword in col for keyword in target_keywords)]

            standard_name = "最後交易日/最後異動日"
            if date_cols:
                # 建立標準欄位，由所有符合條件的欄位按優先順序（或非空值）合併而成
                # 使用 bfill 合併所有日期欄位，優先取最前面非空的
                df[standard_name] = df[date_cols].bfill(axis=1).iloc[:, 0] if not df[date_cols].empty else None

                # 轉換 "最後交易日/最後異動日" 從 月/日/年 至 YYYY/MM/DD
                dt_series = pd.to_datetime(
                    df[standard_name], format="mixed", errors="coerce"
                )

                invalid_dates = df[standard_name][dt_series.isna() & df[standard_name].notna()]
                if not invalid_dates.empty:
                    logging.warning(f"發現 {len(invalid_dates)} 筆無法解析的時間資料。")

                df[standard_name] = dt_series.dt.strftime("%Y/%m/%d")
                # 建立供查詢用的整數欄位，直接填補數值 0 再轉型
                df["transaction_date_int"] = pd.to_numeric(
                    dt_series.dt.strftime("%Y%m%d"), errors="coerce"
                ).fillna(0).astype(int)

            # 確保 NaN 轉為 None，避免 JSON 序列化問題
            df = df.where(pd.notnull(df), None)

            # 將 DataFrame 轉為 Mapping
            # 假設表格中有一欄 "檔案名稱" 或 "品號" 可以作為 Key
            # 若無明確的 "檔案名稱" 欄位，實務上常以 "品號" 與檔名進行前綴比對
            # 這裡我們保留整個 row，在 Service 層進行彈性 Mapping。
            # 我們這裡以 index 作為初步的 fallback，推薦以「品號」或「圖號」為主要的識別依據。

            # 定義預期欄位映射
            # 項次、品號、品名規格、番數、最後交易日/最後異動日、標準成本、備註
            mapped_data = {}
            for _, row in df.iterrows():
                row_dict = row.to_dict()

                # 尋找可以作為檔名配對的可能鍵值
                part_number = str(row_dict.get("品號", ""))
                # 清理空字串或 None
                if part_number and part_number != "None":
                    part_number = part_number.strip()
                    mapped_data[part_number] = row_dict

            logger.info(
                f"Metadata 檔案解析完成，共獲得 {len(mapped_data)} 筆屬性資料。"
            )
            return mapped_data

        except Exception as e:
            logger.error(f"解析 Metadata 檔案失敗: {e}")
            raise RuntimeError(f"解析 Metadata 檔案失敗: {e}")


class VectorRepository:
    """
    向量資料庫存取器。
    對 ChromaDBManager 進行業務封裝，提供檢索與更新的標準介面。
    """

    def __init__(
        self,
        db_path: str = "./chroma_db",
        collection_name: str = "engineering_drawings",
    ):
        """
        初始化向量資料存取器。

        Args:
            db_path (str): ChromaDB 持久化路徑。
            collection_name (str): Collection 名稱。
        """
        self.db = ChromaDBManager(
            db_path=db_path, collection_name=collection_name
        )

    def upsert_vectors(
        self,
        vectors: list[list[float]],
        metadatas: list[dict[str, Any]],
        ids: list[str],
    ) -> None:
        """
        新增或更新向量資料。

        Args:
            vectors: 2048 維 Embedding 列表。
            metadatas: 綁定的屬性字典列表。
            ids: 唯一的 Hash ID 列表。
        """
        self.db.upsert_vectors(vectors=vectors, metadatas=metadatas, ids=ids)

    def query_vectors(
        self,
        query_embeddings: list[list[float]],
        n_results: int = 10,
        where_filter: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """
        查詢相似的向量影像。

        Args:
            query_embeddings: 查詢映像向量。
            n_results: Top K 數量。
            where_filter: ChromaDB 支援的過濾條件結構 (`$and`, `$eq` 等)。

        Returns:
            Dict[str, Any]: 原始 ChromaDB 回傳的字典資料。
        """
        return self.db.query_vectors(
            query_embeddings=query_embeddings,
            n_results=n_results,
            where=where_filter,
        )

    def get_all_categories(self) -> list[str]:
        """
        取得目前所有可用的工程圖類別 (品名規格)。

        Returns:
            List[str]: 所有不重複的類別列表。
        """
        try:
            # 由於 ChromaDB 不直接支援 SELECT DISTINCT，
            # 我們透過 get 取出所有的 metadatas 來進行去重。
            # 注意：在海量資料下可能有性能隱患，實務上可獨立快取或維護獨立關聯表。
            result = self.db.collection.get(include=["metadatas"])
            metadatas = result.get("metadatas", [])

            categories: set[str] = set()
            for meta in metadatas:
                if meta and "type" in meta:
                    cat = meta.get("type")
                    if cat:
                        categories.add(str(cat))

            return sorted(list(categories))

        except Exception as e:
            logger.error(f"取得類別列表失敗: {e}")
            return []
