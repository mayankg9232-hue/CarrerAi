"""Vector embeddings abstraction, deterministic local provider, and cosine similarity calculations."""

from abc import ABC, abstractmethod
import math
import re
from typing import Any, Dict, List, Optional, Sequence
import httpx
import numpy as np

from app.schemas.job import Job
from app.schemas.resume import CandidateProfile


def cosine_similarity(vec1: Sequence[float], vec2: Sequence[float]) -> float:
    """Calculate the cosine similarity between two numeric vectors.

    Returns a float between -1.0 and 1.0 (clamped to [0.0, 1.0] for non-negative embeddings).
    Handles zero-magnitude vectors safely without throwing ZeroDivisionError.
    """
    if not vec1 or not vec2 or len(vec1) != len(vec2):
        return 0.0

    try:
        a = np.asarray(vec1, dtype=np.float32)
        b = np.asarray(vec2, dtype=np.float32)
        norm_a = np.linalg.norm(a)
        norm_b = np.linalg.norm(b)
        if norm_a == 0.0 or norm_b == 0.0:
            return 0.0
        similarity = float(np.dot(a, b) / (norm_a * norm_b))
        return max(0.0, min(1.0, similarity))
    except Exception:
        # Pure Python fallback
        dot = sum(x * y for x, y in zip(vec1, vec2))
        norm_a = math.sqrt(sum(x * x for x in vec1))
        norm_b = math.sqrt(sum(y * y for y in vec2))
        if norm_a == 0.0 or norm_b == 0.0:
            return 0.0
        similarity = dot / (norm_a * norm_b)
        return max(0.0, min(1.0, similarity))


class IEmbeddingProvider(ABC):
    """Abstract interface for generating vector embeddings."""

    @property
    @abstractmethod
    def dimension(self) -> int:
        """Embedding vector dimensionality."""
        pass

    @abstractmethod
    async def embed_text(self, text: str) -> List[float]:
        """Generate a dense embedding vector for a single text string."""
        pass

    @abstractmethod
    async def embed_batch(self, texts: List[str]) -> List[List[float]]:
        """Generate embedding vectors for a batch of text strings."""
        pass


class LocalDeterministicEmbeddingProvider(IEmbeddingProvider):
    """Fast, zero-cost, deterministic local embedding provider for offline tests and MVP retrieval.

    Uses sublinear n-gram frequency hashing with L2 vector normalization. Semantically related
    texts sharing vocabulary produce high cosine similarity, while unrelated texts yield low scores.
    """

    def __init__(self, dimension: int = 384) -> None:
        self._dim = max(64, dimension)

    @property
    def dimension(self) -> int:
        return self._dim

    def _hash_token(self, token: str, dim: int) -> int:
        """Compute a stable hash index for a token."""
        h = 2166136261
        for char in token:
            h = (h ^ ord(char)) * 16777619
        return (h & 0x7FFFFFFF) % dim

    def _tokenize_and_project(self, text: str) -> List[float]:
        """Convert input text into an L2-normalized dense embedding vector."""
        if not text or not text.strip():
            return [0.0] * self._dim

        vector = [0.0] * self._dim
        words = re.findall(r"\b[a-zA-Z0-9_\-\.#+]+\b", text.lower())

        if not words:
            return [0.0] * self._dim

        # 1. Unigram and Bigram hashing
        for i, word in enumerate(words):
            idx1 = self._hash_token(word, self._dim)
            vector[idx1] += 1.0

            # Character 3-grams for typo resilience
            if len(word) >= 3:
                for j in range(len(word) - 2):
                    trigram = word[j : j + 3]
                    idx_tri = self._hash_token(trigram, self._dim)
                    vector[idx_tri] += 0.2

            # Bigram for phrase context
            if i + 1 < len(words):
                bigram = f"{word}_{words[i+1]}"
                idx2 = self._hash_token(bigram, self._dim)
                vector[idx2] += 0.5

        # 2. Sublinear term frequency scaling: 1 + log(tf)
        for i in range(self._dim):
            if vector[i] > 0.0:
                vector[i] = 1.0 + math.log(vector[i])

        # 3. L2 Unit Normalization
        norm = math.sqrt(sum(v * v for v in vector))
        if norm > 0.0:
            vector = [v / norm for v in vector]

        return vector

    async def embed_text(self, text: str) -> List[float]:
        return self._tokenize_and_project(text)

    async def embed_batch(self, texts: List[str]) -> List[List[float]]:
        return [self._tokenize_and_project(t) for t in texts]


class GeminiEmbeddingProvider(IEmbeddingProvider):
    """Google Gemini Embedding provider supporting Gemini Embedding 2 (gemini-embedding-2).

    Uses asynchronous HTTP requests via httpx to compute high-fidelity 3072-dimensional
    embeddings for individual texts and batches with error handling and rate-limit resilience.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: str = "gemini-embedding-2",
        base_url: str = "https://generativelanguage.googleapis.com/v1beta",
        timeout: float = 20.0,
        transport: Optional[httpx.AsyncBaseTransport] = None,
    ) -> None:
        self.api_key = api_key
        raw = model.strip() if model else "gemini-embedding-2"
        self.model_short = raw.replace("models/", "")
        self.base_url = base_url.rstrip("/")
        self.timeout = max(1.0, float(timeout))
        self.transport = transport
        self._dim = 3072

    @property
    def dimension(self) -> int:
        return self._dim

    async def embed_text(self, text: str) -> List[float]:
        """Generate a dense embedding vector for a single text using Gemini embedContent."""
        if not text or not text.strip():
            return [0.0] * self._dim

        if not self.api_key:
            raise ValueError("Gemini API key is required for GeminiEmbeddingProvider.")

        url = f"{self.base_url}/models/{self.model_short}:embedContent?key={self.api_key}"
        payload = {
            "content": {
                "parts": [{"text": text}]
            }
        }

        async with httpx.AsyncClient(timeout=self.timeout, transport=self.transport) as client:
            resp = await client.post(url, json=payload)
            if resp.status_code == 200:
                data = resp.json()
                values = data.get("embedding", {}).get("values", [])
                if values:
                    self._dim = len(values)
                    return values
                return [0.0] * self._dim
            elif resp.status_code == 429:
                raise RuntimeError(f"Gemini embedding rate limit exceeded: {resp.text}")
            elif resp.status_code in (401, 403):
                raise RuntimeError(f"Gemini embedding authentication error: {resp.text}")
            else:
                raise RuntimeError(f"Gemini embedding API error ({resp.status_code}): {resp.text}")

    async def embed_batch(self, texts: List[str]) -> List[List[float]]:
        """Generate embedding vectors for a batch of strings using Gemini batchEmbedContents."""
        if not texts:
            return []

        if not self.api_key:
            raise ValueError("Gemini API key is required for GeminiEmbeddingProvider.")

        results: List[List[float]] = []
        batch_size = 100

        async with httpx.AsyncClient(timeout=self.timeout, transport=self.transport) as client:
            for i in range(0, len(texts), batch_size):
                chunk = texts[i : i + batch_size]
                requests_payload = []
                for t in chunk:
                    clean_t = t if t and t.strip() else " "
                    requests_payload.append({
                        "model": f"models/{self.model_short}",
                        "content": {"parts": [{"text": clean_t}]}
                    })

                url = f"{self.base_url}/models/{self.model_short}:batchEmbedContents?key={self.api_key}"
                resp = await client.post(url, json={"requests": requests_payload})
                if resp.status_code == 200:
                    data = resp.json()
                    raw_embeddings = data.get("embeddings", [])
                    for idx, emb_item in enumerate(raw_embeddings):
                        orig_text = chunk[idx]
                        if not orig_text or not orig_text.strip():
                            results.append([0.0] * self._dim)
                        else:
                            vals = emb_item.get("values", [])
                            if vals:
                                self._dim = len(vals)
                            results.append(vals or [0.0] * self._dim)
                elif resp.status_code == 429:
                    raise RuntimeError(f"Gemini embedding rate limit exceeded: {resp.text}")
                elif resp.status_code in (401, 403):
                    raise RuntimeError(f"Gemini embedding authentication error: {resp.text}")
                else:
                    raise RuntimeError(f"Gemini batch embedding API error ({resp.status_code}): {resp.text}")

        return results


def get_embedding_provider(
    provider_name: Optional[str] = None,
    api_key: Optional[str] = None,
    model: Optional[str] = None,
    transport: Optional[httpx.AsyncBaseTransport] = None,
) -> IEmbeddingProvider:
    """Factory returning configured vector embedding provider.

    Returns GeminiEmbeddingProvider when provider is 'gemini' and an API key is present.
    Falls back safely to LocalDeterministicEmbeddingProvider when offline or unconfigured.
    """
    from app.core.config import Settings
    settings = Settings()

    prov = (provider_name or settings.EMBEDDING_PROVIDER or "local").lower()
    key = api_key if api_key is not None else settings.EMBEDDING_API_KEY
    mdl = model or settings.EMBEDDING_MODEL or "gemini-embedding-2"

    if prov == "gemini" and key:
        return GeminiEmbeddingProvider(api_key=key, model=mdl, transport=transport)

    return LocalDeterministicEmbeddingProvider()


# ==============================================================================
# CANONICAL TEXT BUILDERS FOR EMBEDDINGS
# ==============================================================================

def prepare_candidate_embedding_text(candidate: CandidateProfile) -> str:
    """Compose structured candidate profile into dense semantic representation."""
    parts = []
    if candidate.summary:
        parts.append(f"Summary: {candidate.summary}")

    if candidate.skills:
        skill_names = [s.name for s in candidate.skills]
        parts.append(f"Skills: {', '.join(skill_names)}")

    if candidate.experience:
        exp_entries = [f"{e.title} at {e.company} - {e.description or ''}" for e in candidate.experience]
        parts.append(f"Experience: {' | '.join(exp_entries)}")

    if candidate.education:
        edu_entries = [f"{e.degree} from {e.institution}" for e in candidate.education]
        parts.append(f"Education: {' | '.join(edu_entries)}")

    return "\n".join(parts)


def prepare_job_embedding_text(job: Job) -> str:
    """Compose structured job listing into dense semantic representation."""
    parts = [
        f"Title: {job.title}",
        f"Company: {job.company}",
        f"Location: {job.location}",
    ]
    if job.required_skills:
        parts.append(f"Required Skills: {', '.join(job.required_skills)}")
    if job.preferred_skills:
        parts.append(f"Preferred Skills: {', '.join(job.preferred_skills)}")
    if job.experience_level:
        parts.append(f"Experience Level: {job.experience_level}")
    if job.description:
        parts.append(f"Description: {job.description}")

    return "\n".join(parts)
