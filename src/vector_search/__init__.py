"""
Vector Search Module
初始化模組，暴露主要類別。
"""

from .database import ChromaDBManager
from .engine import MaxPoolingStrategy, RetrievalEngine, WeightedSumStrategy
from .feature_extractor import SimSiamFeatureExtractor
from .indexer import ImageIndexer
from .interfaces import BaseEvaluator, ScoreAggregationStrategy
from .router import VectorSearchRouter, fetch_router_instance

__all__ = [
    "BaseEvaluator",
    "ScoreAggregationStrategy",
    "ChromaDBManager",
    "SimSiamFeatureExtractor",
    "RetrievalEngine",
    "WeightedSumStrategy",
    "MaxPoolingStrategy",
    "ImageIndexer",
    "VectorSearchRouter",
    "fetch_router_instance",
]
