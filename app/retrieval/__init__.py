"""Retrieval, embeddings, and RAG context assembly package (Developer B)."""

from app.retrieval.embeddings import (
    GeminiEmbeddingProvider,
    IEmbeddingProvider,
    LocalDeterministicEmbeddingProvider,
    cosine_similarity,
    get_embedding_provider,
    prepare_candidate_embedding_text,
    prepare_job_embedding_text,
)
from app.retrieval.rag_service import RAGContextAssembler
from app.retrieval.retriever import JobRetriever

__all__ = [
    "GeminiEmbeddingProvider",
    "IEmbeddingProvider",
    "LocalDeterministicEmbeddingProvider",
    "cosine_similarity",
    "get_embedding_provider",
    "prepare_candidate_embedding_text",
    "prepare_job_embedding_text",
    "JobRetriever",
    "RAGContextAssembler",
]
