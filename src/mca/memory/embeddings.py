"""Embedding generation via external provider (Ollama or OpenAI-compatible).

Default: Ollama with nomic-embed-text (768 dimensions).
"""
from __future__ import annotations

import os
from typing import Any

import httpx

from mca.log import get_logger

log = get_logger("embeddings")

EMBED_DIM = 768  # nomic-embed-text default


class EmbeddingError(Exception):
    """Raised when embedding generation fails."""


class Embedder:
    """Generate embeddings via an external API.

    Supports:
    - Ollama API: POST /api/embeddings (default)
    - OpenAI-compatible: POST /v1/embeddings

    Env vars:
        EMBEDDING_BASE_URL  → http://localhost:11434 (Ollama)
        EMBEDDING_MODEL     → nomic-embed-text
    """

    def __init__(
        self,
        base_url: str | None = None,
        model: str | None = None,
    ) -> None:
        self.base_url = (
            base_url
            or os.environ.get("EMBEDDING_BASE_URL")
            or "http://localhost:11434"
        ).rstrip("/")
        self.model = (
            model
            or os.environ.get("EMBEDDING_MODEL")
            or "nomic-embed-text"
        )
        self._is_ollama = "11434" in self.base_url or "/api" in self.base_url
        self._client = httpx.Client(timeout=httpx.Timeout(connect=5.0, read=30.0, write=10.0, pool=5.0))

    def embed(self, text: str) -> list[float]:
        """Generate embedding for a single text. Returns float vector."""
        if self._is_ollama:
            return self._embed_ollama(text)
        return self._embed_openai(text)

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Generate embeddings for multiple texts."""
        return [self.embed(t) for t in texts]

    def _embed_ollama(self, text: str) -> list[float]:
        """Ollama API: POST /api/embeddings."""
        try:
            resp = self._client.post(
                f"{self.base_url}/api/embeddings",
                json={"model": self.model, "prompt": text},
            )
            resp.raise_for_status()
            data = resp.json()
            embedding = data.get("embedding", [])
            if not embedding:
                raise EmbeddingError(f"Empty embedding from Ollama: {data}")
            return embedding
        except httpx.HTTPError as e:
            raise EmbeddingError(f"Ollama embedding failed: {e}") from e

    def _embed_openai(self, text: str) -> list[float]:
        """OpenAI-compatible API: POST /v1/embeddings."""
        try:
            resp = self._client.post(
                f"{self.base_url}/v1/embeddings",
                json={"model": self.model, "input": text},
                headers={"Authorization": f"Bearer {os.environ.get('EMBEDDING_API_KEY', 'not-needed')}"},
            )
            resp.raise_for_status()
            data = resp.json()
            return data["data"][0]["embedding"]
        except (httpx.HTTPError, KeyError, IndexError) as e:
            raise EmbeddingError(f"OpenAI embedding failed: {e}") from e

    def close(self) -> None:
        self._client.close()


def get_embedder(config: Any = None) -> Embedder:
    """Factory: create Embedder from config or env vars."""
    if config and hasattr(config, "memory"):
        mem_cfg = config.memory
        return Embedder(
            base_url=mem_cfg.get("embedding_base_url"),
            model=mem_cfg.get("embedding_model"),
        )
    return Embedder()
