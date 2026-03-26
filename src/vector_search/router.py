"""
Transport Layer: Router for Vector Search 2.
只處理 Request/Response，絕對禁止包含業務規則。
提供與舊版相同的 API，以便 demo 服務無縫升級。
"""
import logging
from typing import Any

from src.vector_search.database import ChromaDBManager
from src.vector_search.engine import RetrievalEngine, WeightedSumStrategy
from src.vector_search.feature_extractor import SimSiamFeatureExtractor

logger = logging.getLogger(__name__)

class VectorSearchRouter:
    """
    Vector Search Router.

    作為模組的外部入口，封裝 Service / Engine 層調用。
    相容於舊版的介面。
    """

    def __init__(
        self,
        db_storage_path: str,
        model_checkpoint_path: str,
        collection_name: str = "engineering_drawings",
    ):
        self.repository = ChromaDBManager(
            db_path=db_storage_path, collection_name=collection_name
        )
        self.extraction_service = SimSiamFeatureExtractor(
            model_path=model_checkpoint_path,
            # In image_preprocessing3 we force img to [1, 512, 512] for SimSiam
            # Default backbone='resnet50', in_channels=3 for FeatureExtractor
            # but model fallback depends on your trained model
        )
        self.retrieval_engine = RetrievalEngine(
            db_manager=self.repository,
            feature_extractor=self.extraction_service,
            aggregation_strategy=WeightedSumStrategy()
        )

    def execute_image_search_by_path(
        self,
        query_image_path: str,
        max_result_count: int = 20
    ) -> list[dict[str, Any]]:
        """
        透過圖片路徑執行搜尋。
        舊版格式回傳：list['parent_pdf_id', 'relevance_score',
        'matched_component_count', 'path']
        """
        results = self.retrieval_engine.retrieve(
            query_image_path, top_k=max_result_count
        )

        # 轉換結果為舊版 service.py 所預期的格式
        formatted_results = []
        for res in results:
            # 取得原始路徑
            path = ""
            details = res.get("details", [])
            if details and isinstance(details, list) and len(details) > 0:
                path = details[0].get("path", "")

            formatted_results.append({
                "parent_pdf_id": res["parent_pdf_id"],
                "relevance_score": res["score"],
                "matched_component_count": res.get("accumulated_matches", 0),
                "path": path,
            })

        return formatted_results

def fetch_router_instance(
    db_storage_path: str,
    model_checkpoint_path: str,
    collection_name: str = "engineering_drawings",
) -> VectorSearchRouter:
    return VectorSearchRouter(
        db_storage_path, model_checkpoint_path, collection_name
    )
