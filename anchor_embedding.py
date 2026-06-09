"""Embedding abstraction layer for Anchor Memory.

Supports two modes:
  - local:        sentence-transformers (default, zero API cost)
  - openai:       OpenAI Embeddings API (text-embedding-3-small etc.)
  - openai-compat: Any OpenAI-compatible /v1/embeddings endpoint

Resolution order for `get_embedder()`:
  1. Explicit parameters passed by caller
  2. ANCHOR_EMBEDDING env var (format: "provider/model" e.g. "openai/text-embedding-3-small")
  3. ~/.anchor/config.yaml embedding section
  4. Fallback: openai-compat / text-embedding-3-small
"""
from __future__ import annotations

import os
import logging
from pathlib import Path
from typing import Optional

log = logging.getLogger("anchor_embedding")
CONFIG_DIR = Path.home() / ".anchor"


class Embedder:
    """Provider-agnostic embedding interface."""

    provider: str = "unknown"
    dimension: int = 0

    def encode(self, text: str) -> list[float]:
        raise NotImplementedError

    def encode_batch(self, texts: list[str]) -> list[list[float]]:
        raise NotImplementedError


class LocalEmbedder(Embedder):
    """Local sentence-transformers embedder."""

    provider = "local"

    def __init__(self, model_name: str = "paraphrase-multilingual-MiniLM-L12-v2"):
        from sentence_transformers import SentenceTransformer
        self._model = SentenceTransformer(model_name)
        self.model_name = model_name
        test = self._model.encode(["test"])
        self.dimension = len(test[0])
        log.info("LocalEmbedder loaded: %s (dim=%d)", model_name, self.dimension)

    def encode(self, text: str) -> list[float]:
        return self._model.encode(text).tolist()

    def encode_batch(self, texts: list[str]) -> list[list[float]]:
        return self._model.encode(texts).tolist()


class OpenAIEmbedder(Embedder):
    """OpenAI or OpenAI-compatible /v1/embeddings embedder."""

    provider = "openai"

    def __init__(self, model: str = "text-embedding-3-small",
                 api_key: Optional[str] = None,
                 endpoint: Optional[str] = None,
                 dimensions: Optional[int] = None):
        try:
            from openai import OpenAI
        except ImportError as e:
            raise ImportError(
                "openai SDK not installed. Run: pip install openai"
            ) from e

        self.model = model
        self._client = OpenAI(
            api_key=api_key,
            base_url=endpoint,
        )
        self._dimensions = dimensions

        try:
            test_resp = self._client.embeddings.create(
                input=["test"], model=self.model,
                dimensions=dimensions if dimensions else None,
            )
            self.dimension = len(test_resp.data[0].embedding)
        except Exception:
            self.dimension = 1536
            log.warning("OpenAIEmbedder test call failed, using default dimension=%d", self.dimension)
        display = f"{endpoint}/{model}" if endpoint else model
        log.info("OpenAIEmbedder ready: %s (dim=%d)", display, self.dimension)

    def encode(self, text: str) -> list[float]:
        resp = self._client.embeddings.create(
            input=[text], model=self.model,
            dimensions=self._dimensions if self._dimensions else None,
        )
        return resp.data[0].embedding

    def encode_batch(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        resp = self._client.embeddings.create(
            input=texts, model=self.model,
            dimensions=self._dimensions if self._dimensions else None,
        )
        return [d.embedding for d in sorted(resp.data, key=lambda x: x.index)]


def _load_config_yaml():
    """Read embedding config from ~/.anchor/config.yaml if present."""
    try:
        import yaml
    except ImportError:
        return {}
    cfg_path = CONFIG_DIR / "config.yaml"
    if not cfg_path.exists():
        return {}
    try:
        data = yaml.safe_load(cfg_path.read_text())
        return data.get("embedding") or {}
    except Exception:
        return {}


def _parse_env():
    """Parse ANCHOR_EMBEDDING env var. Returns (provider, model) or (None, None)."""
    spec = os.environ.get("ANCHOR_EMBEDDING", "").strip()
    if not spec:
        return None, None
    if "/" in spec:
        provider, model = spec.split("/", 1)
        return provider.strip(), model.strip()
    return spec.strip(), None


def get_embedder(
    provider: Optional[str] = None,
    model: Optional[str] = None,
    api_key: Optional[str] = None,
    endpoint: Optional[str] = None,
    dimensions: Optional[int] = None,
) -> Embedder:
    """Get an Embedder instance based on configuration.

    Resolution:
      1. Explicit provider arg
      2. ANCHOR_EMBEDDING env var ("provider/model")
      3. ~/.anchor/config.yaml embedding section
      4. Fallback: openai-compat / text-embedding-3-small
    """
    env_provider, env_model = _parse_env()
    yaml_cfg = _load_config_yaml()

    final_provider = provider or env_provider or yaml_cfg.get("provider") or "openai-compat"
    final_model = model or env_model or yaml_cfg.get("model") or "text-embedding-3-small"

    if final_provider == "local":
        model_name = final_model or "paraphrase-multilingual-MiniLM-L12-v2"
        return LocalEmbedder(model_name=model_name)

    if final_provider in ("openai", "openai-compat"):
        final_api_key = (
            api_key
            or os.environ.get("ANCHOR_EMBEDDING_API_KEY")
            or yaml_cfg.get("api_key")
        )
        if not final_api_key:
            log.warning(
                "ANCHOR_EMBEDDING_API_KEY not set. Embedding features will be unavailable until configured."
            )
            return None
        final_endpoint = endpoint or os.environ.get("ANCHOR_EMBEDDING_ENDPOINT") or yaml_cfg.get("endpoint")
        model_name = final_model or "text-embedding-3-small"
        return OpenAIEmbedder(
            model=model_name,
            api_key=final_api_key,
            endpoint=final_endpoint,
            dimensions=dimensions,
        )

    raise ValueError(
        f"Unknown embedding provider '{final_provider}'. "
        "Supported: local, openai, openai-compat"
    )
