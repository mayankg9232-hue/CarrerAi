"""Retrieval, embeddings, and RAG context assembly package (Developer B)."""

from app.retrieval.embeddings import (
    IEmbeddingProvider,
    LocalDeterministicEmbeddingProvider,
    cosine_similarity,
    prepare_candidate_embedding_text,
    prepare_job_embedding_text,
)
from app.retrieval.rag_service import RAGContextAssembler
from app.retrieval.retriever import JobRetriever

__all__ = [
    "IEmbeddingProvider",
    "LocalDeterministicEmbeddingProvider",
    "cosine_similarity",
    "prepare_candidate_embedding_text",
    "prepare_job_embedding_text",
    "JobRetriever",
    "RAGContextAssembler",
]
