"""Unit and Integration Tests for Gemini Embedding 2 Provider."""

import json
import pytest
import httpx

from app.core.config import Settings
from app.retrieval.embeddings import (
    GeminiEmbeddingProvider,
    LocalDeterministicEmbeddingProvider,
    cosine_similarity,
    get_embedding_provider,
)


# ==============================================================================
# MOCKED TESTS (FAST, ISOLATED, ZERO-NETWORK)
# ==============================================================================

@pytest.mark.asyncio
async def test_gemini_embed_text_success_mocked() -> None:
    """Verify embed_text correctly queries Gemini API and parses 3072-dim vector."""
    fake_vector = [0.01 * (i % 10) for i in range(3072)]

    def handle_request(request: httpx.Request) -> httpx.Response:
        assert "models/gemini-embedding-2:embedContent" in str(request.url)
        assert "key=fake-gemini-key" in str(request.url)
        body = json.loads(request.content)
        assert body["content"]["parts"][0]["text"] == "Python FastAPI developer"
        return httpx.Response(200, json={"embedding": {"values": fake_vector}})

    transport = httpx.MockTransport(handle_request)
    provider = GeminiEmbeddingProvider(
        api_key="fake-gemini-key",
        model="gemini-embedding-2",
        transport=transport,
    )

    vector = await provider.embed_text("Python FastAPI developer")
    assert len(vector) == 3072
    assert vector == fake_vector
    assert provider.dimension == 3072


@pytest.mark.asyncio
async def test_gemini_embed_batch_success_mocked() -> None:
    """Verify embed_batch formats batchEmbedContents and parses all results."""
    vec1 = [0.1] * 3072
    vec2 = [0.2] * 3072

    def handle_request(request: httpx.Request) -> httpx.Response:
        assert "models/gemini-embedding-2:batchEmbedContents" in str(request.url)
        body = json.loads(request.content)
        assert len(body["requests"]) == 2
        assert body["requests"][0]["model"] == "models/gemini-embedding-2"
        return httpx.Response(200, json={"embeddings": [{"values": vec1}, {"values": vec2}]})

    transport = httpx.MockTransport(handle_request)
    provider = GeminiEmbeddingProvider(
        api_key="fake-gemini-key",
        model="gemini-embedding-2",
        transport=transport,
    )

    results = await provider.embed_batch(["Backend engineer", "Data scientist"])
    assert len(results) == 2
    assert len(results[0]) == 3072
    assert len(results[1]) == 3072
    assert results[0] == vec1
    assert results[1] == vec2


@pytest.mark.asyncio
async def test_gemini_embed_empty_or_whitespace_returns_zero_vector() -> None:
    """Verify empty/whitespace string immediately returns zero vector without making an API call."""
    called = False

    def handle_request(request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(200, json={})

    transport = httpx.MockTransport(handle_request)
    provider = GeminiEmbeddingProvider(
        api_key="fake-gemini-key",
        model="gemini-embedding-2",
        transport=transport,
    )

    v_empty = await provider.embed_text("")
    v_spaces = await provider.embed_text("     \n  ")
    assert called is False
    assert len(v_empty) == 3072
    assert all(x == 0.0 for x in v_empty)
    assert len(v_spaces) == 3072
    assert all(x == 0.0 for x in v_spaces)


@pytest.mark.asyncio
async def test_gemini_embed_missing_api_key_raises_error() -> None:
    """Verify calling embedding without an API key raises ValueError."""
    provider = GeminiEmbeddingProvider(api_key=None)
    with pytest.raises(ValueError, match="Gemini API key is required"):
        await provider.embed_text("Some technical text")

    with pytest.raises(ValueError, match="Gemini API key is required"):
        await provider.embed_batch(["Some technical text"])


@pytest.mark.asyncio
async def test_gemini_embed_rate_limit_error_handling() -> None:
    """Verify HTTP 429 response is translated to rate limit RuntimeError."""
    def handle_request(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, text="Resource has been exhausted (rate limit)")

    transport = httpx.MockTransport(handle_request)
    provider = GeminiEmbeddingProvider(api_key="fake-gemini-key", transport=transport)

    with pytest.raises(RuntimeError, match="rate limit exceeded"):
        await provider.embed_text("Some text")


@pytest.mark.asyncio
async def test_gemini_embed_auth_error_handling() -> None:
    """Verify HTTP 401/403 response is translated to authentication RuntimeError."""
    def handle_request(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, text="API_KEY_INVALID")

    transport = httpx.MockTransport(handle_request)
    provider = GeminiEmbeddingProvider(api_key="invalid-key", transport=transport)

    with pytest.raises(RuntimeError, match="authentication error"):
        await provider.embed_text("Some text")


def test_get_embedding_provider_factory() -> None:
    """Verify factory returns GeminiEmbeddingProvider when key is present and Local fallback when unconfigured."""
    # Configured with Gemini and key
    p_gemini = get_embedding_provider(
        provider_name="gemini",
        api_key="test-key-123",
        model="gemini-embedding-2",
    )
    assert isinstance(p_gemini, GeminiEmbeddingProvider)
    assert p_gemini.api_key == "test-key-123"
    assert p_gemini.model_short == "gemini-embedding-2"

    # Offline / local fallback
    p_local = get_embedding_provider(
        provider_name="local",
        api_key=None,
    )
    assert isinstance(p_local, LocalDeterministicEmbeddingProvider)

    # Empty key fallback
    p_fallback = get_embedding_provider(
        provider_name="gemini",
        api_key="",
    )
    assert isinstance(p_fallback, LocalDeterministicEmbeddingProvider)


def test_cosine_similarity_with_3072_dim_vectors() -> None:
    """Verify cosine similarity behaves accurately with high-dimensional 3072 embeddings."""
    v1 = [1.0] * 3072
    v2 = [1.0] * 3072
    assert abs(cosine_similarity(v1, v2) - 1.0) < 1e-5

    v_zero = [0.0] * 3072
    assert cosine_similarity(v1, v_zero) == 0.0


# ==============================================================================
# LIVE INTEGRATION TEST (RUNS WITH CONFIGURED API KEY)
# ==============================================================================

@pytest.mark.asyncio
async def test_gemini_embedding_live_verification() -> None:
    """Live verification of Gemini Embedding 2 with the active API key."""
    settings = Settings()
    if not settings.EMBEDDING_API_KEY:
        pytest.skip("EMBEDDING_API_KEY not configured")

    provider = GeminiEmbeddingProvider(
        api_key=settings.EMBEDDING_API_KEY,
        model=settings.EMBEDDING_MODEL or "gemini-embedding-2",
    )

    text1 = "Senior Software Engineer specialized in Python, FastAPI, and Kubernetes"
    text2 = "Senior Python Developer with microservices and container orchestration experience"
    text3 = "Registered Nurse with 5 years clinical emergency room patient care experience"

    # Test single embed
    try:
        vec1 = await provider.embed_text(text1)
    except httpx.ConnectError:
        pytest.skip("Network connection to Gemini API unavailable")
    except RuntimeError as exc:
        if "quota" in str(exc).lower() or "rate limit" in str(exc).lower():
            pytest.skip("Gemini free-tier quota exhausted, skipping live test")
        raise
    assert len(vec1) == 3072
    assert any(abs(x) > 1e-4 for x in vec1)

    # Test batch embed
    batch_vectors = await provider.embed_batch([text2, text3])
    assert len(batch_vectors) == 2
    vec2, vec3 = batch_vectors[0], batch_vectors[1]
    assert len(vec2) == 3072
    assert len(vec3) == 3072

    # Semantic similarity checks
    sim_related = cosine_similarity(vec1, vec2)
    sim_unrelated = cosine_similarity(vec1, vec3)

    assert sim_related > 0.60, f"Expected high similarity between related tech profiles, got {sim_related}"
    assert sim_unrelated < sim_related, f"Unrelated nursing profile ({sim_unrelated}) must score lower than tech profile ({sim_related})"
