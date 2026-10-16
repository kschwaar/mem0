from abc import ABC, abstractmethod
from typing import Any, Dict, List

from mem0.runtime_tracing import trace_call


class BaseReranker(ABC):
    """Abstract base class for all rerankers."""

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        method = cls.__dict__.get("rerank")
        if method is not None:
            cls.rerank = trace_call("rerank", "reranker")(method)
    
    @abstractmethod
    def rerank(self, query: str, documents: List[Dict[str, Any]], top_k: int = None) -> List[Dict[str, Any]]:
        """
        Rerank documents based on relevance to the query.
        
        Args:
            query: The search query
            documents: List of documents to rerank, each with 'memory' field  
            top_k: Number of top documents to return (None = return all)
            
        Returns:
            List of reranked documents with added 'rerank_score' field
        """
        pass
