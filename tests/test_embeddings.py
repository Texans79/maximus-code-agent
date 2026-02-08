"""Tests for embeddings — unit tests with mocked HTTP + live Ollama integration."""
import json
import os

import httpx
import pytest

from mca.memory.embeddings import Embedder, EmbeddingError, EMBED_DIM


# ── Unit Tests (mocked HTTP) ────────────────────────────────────────────────

class FakeTransport(httpx.BaseTransport):
    def __init__(self, response_body: dict, status: int = 200):
        self._body = response_body
        self._status = status

    def handle_request(self, request):
        return httpx.Response(self._status, content=json.dumps(self._body).encode())


def _make_embedder(response_body: dict, status: int = 200) -> Embedder:
    emb = Embedder(base_url="http://fake:11434", model="test-model")
    emb._client = httpx.Client(transport=FakeTransport(response_body, status), timeout=5)
    return emb


class TestEmbedMocked:
    def test_ollama_embed(self):
        vec = [0.1] * 768
        emb = _make_embedder({"embedding": vec})
        result = emb.embed("hello world")
        assert len(result) == 768
        assert result[0] == 0.1

    def test_ollama_empty_embedding_raises(self):
        emb = _make_embedder({"embedding": []})
        with pytest.raises(EmbeddingError, match="Empty embedding"):
            emb.embed("hello")

    def test_ollama_http_error_raises(self):
        emb = _make_embedder({"error": "model not found"}, status=404)
        with pytest.raises(EmbeddingError):
            emb.embed("hello")

    def test_embed_batch(self):
        vec = [0.5] * 768
        emb = _make_embedder({"embedding": vec})
        results = emb.embed_batch(["hello", "world"])
        assert len(results) == 2
        assert len(results[0]) == 768


# ── Live Ollama Integration Tests ───────────────────────────────────────────

def _ollama_available() -> bool:
    try:
        resp = httpx.get("http://localhost:11434/api/tags", timeout=3)
        return resp.status_code == 200
    except Exception:
        return False


live = pytest.mark.skipif(not _ollama_available(), reason="Ollama not available")


@live
class TestLiveOllama:
    def test_embed_real(self):
        emb = Embedder()
        vec = emb.embed("PostgreSQL is the primary database backend")
        assert len(vec) == EMBED_DIM
        assert all(isinstance(v, float) for v in vec)
        emb.close()

    def test_embed_batch_real(self):
        emb = Embedder()
        vecs = emb.embed_batch(["hello", "world"])
        assert len(vecs) == 2
        assert len(vecs[0]) == EMBED_DIM
        emb.close()
