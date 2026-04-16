"""
vLLM client — async OpenAI-compatible interface for MedGemma inference.

Supports two modes:
  - **local**: medgemma-4b-it on RTX 5060 (8GB VRAM), vLLM server on :8001
  - **cloud**: medgemma-27b-it (AWQ) on RunPod/Modal, remote endpoint

The client duck-types with the existing MedGemmaRunnable agent interface
(process_query, chat) so it drops in without changing the simulation engine.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# ── Defaults ──────────────────────────────────────────────────────────────────

_DEFAULT_LOCAL_URL = "http://localhost:8001/v1"
_DEFAULT_LOCAL_MODEL = "google/medgemma-4b-it"
_DEFAULT_CLOUD_MODEL = "google/medgemma-27b-it"

# Debug flag for logging full payloads (off by default - potential data exposure)
_DEBUG_LOG_PAYLOAD = os.getenv("DEBUG_LOG_PAYLOAD", "").lower() in ("true", "1", "yes")


class VLLMClient:
    """
    Async + sync OpenAI-compatible client for vLLM-served models.

    Usage
    -----
    ::

        client = VLLMClient.from_env()           # reads VLLM_* env vars
        # Async
        text = await client.chat_async(messages)
        # Sync (for LangChain Runnable.invoke)
        text = client.chat(messages)
    """

    def __init__(
        self,
        base_url: str,
        api_key: str = "EMPTY",
        model: str = _DEFAULT_LOCAL_MODEL,
        timeout: float = 30.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        self._headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

    # ── Constructors ──────────────────────────────────────────────────────────

    @classmethod
    def from_env(cls) -> VLLMClient | None:
        """
        Build a VLLMClient from environment variables.

        Env vars:
          VLLM_MODE        = local | cloud | simulated   (default: simulated)
          VLLM_LOCAL_URL    = http://localhost:8001/v1
          VLLM_CLOUD_URL    = https://your-pod.runpod.ai/v1
          VLLM_CLOUD_API_KEY = rp_xxx
          VLLM_MODEL        = override model name

        Returns None if mode is 'simulated'.
        """
        mode = os.getenv("VLLM_MODE", "simulated").lower()

        if mode == "simulated":
            logger.info("VLLM_MODE=simulated — running in keyword-based mode")
            return None

        if mode == "local":
            base_url = os.getenv("VLLM_LOCAL_URL", _DEFAULT_LOCAL_URL)
            model = os.getenv("VLLM_MODEL", _DEFAULT_LOCAL_MODEL)
            api_key = "EMPTY"
            logger.info("VLLM_MODE=local — connecting to %s model=%s", base_url, model)

        elif mode == "cloud":
            base_url = os.getenv("VLLM_CLOUD_URL", "")
            api_key = os.getenv("VLLM_CLOUD_API_KEY", "")
            model = os.getenv("VLLM_MODEL", _DEFAULT_CLOUD_MODEL)
            if not base_url:
                logger.error("VLLM_MODE=cloud but VLLM_CLOUD_URL is not set")
                return None
            logger.info("VLLM_MODE=cloud — connecting to %s model=%s", base_url, model)

        else:
            logger.warning("Unknown VLLM_MODE=%s — falling back to simulated", mode)
            return None

        return cls(base_url=base_url, api_key=api_key, model=model)

    # ── Async interface ───────────────────────────────────────────────────────

    async def chat_async(
        self,
        messages: list[dict[str, str]],
        temperature: float = 0.7,
        max_tokens: int = 512,
        timeout: float | None = None,
    ) -> str:
        """Send a chat completion request and return the response text."""
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        logger.info("vLLM request: url=%s, model=%s, messages_count=%d, max_tokens=%d",
                   self.base_url, self.model, len(messages), max_tokens)
        if _DEBUG_LOG_PAYLOAD:
            logger.debug("vLLM payload: %s", payload)
        else:
            logger.debug("vLLM payload: [redacted - set DEBUG_LOG_PAYLOAD=1 to log]")
        async with httpx.AsyncClient(timeout=timeout or self.timeout) as client:
            try:
                response = await client.post(
                    f"{self.base_url}/chat/completions",
                    headers=self._headers,
                    json=payload,
                )
                logger.info("vLLM response: status=%d", response.status_code)
                if response.status_code != 200:
                    logger.error("vLLM error body: %s", response.text)
                response.raise_for_status()
                data = response.json()
                return data["choices"][0]["message"]["content"]
            except Exception as e:
                logger.error("vLLM request failed: %s", e)
                raise

    async def generate_async(
        self,
        prompt: str,
        temperature: float = 0.7,
        max_tokens: int = 512,
        timeout: float | None = None,
    ) -> str:
        """Single-turn generation from a raw prompt string."""
        return await self.chat_async(
            [{"role": "user", "content": prompt}],
            temperature=temperature,
            max_tokens=max_tokens,
            timeout=timeout,
        )

    async def health_async(self) -> bool:
        """Check if the vLLM server is reachable."""
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                r = await client.get(f"{self.base_url}/models", headers=self._headers)
                return r.status_code == 200
        except Exception:
            return False

    # ── Sync interface (for LangChain Runnable.invoke) ────────────────────────

    def sync_chat_messages(
        self,
        messages: list[dict[str, str]],
        temperature: float = 0.7,
        max_tokens: int = 512,
    ) -> str:
        """
        Synchronous, role-preserving chat — passes system/user/assistant messages
        directly to the OpenAI-compatible API instead of flattening to one string.
        Preferred by MedGemmaRunnable for patient-persona turns.
        """
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop and loop.is_running():
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as pool:
                future = pool.submit(
                    asyncio.run,
                    self.chat_async(messages, temperature=temperature, max_tokens=max_tokens),
                )
                return future.result(timeout=self.timeout)
        else:
            return asyncio.run(
                self.chat_async(messages, temperature=temperature, max_tokens=max_tokens)
            )

    def chat(self, prompt: str) -> str:
        """
        Synchronous single-turn generation.
        Used by MedGemmaRunnable.invoke() which calls agent.chat().
        """
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop and loop.is_running():
            # We're inside an async context (e.g. FastAPI) — run in a thread
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as pool:
                future = pool.submit(asyncio.run, self.generate_async(prompt))
                return future.result(timeout=self.timeout)
        else:
            return asyncio.run(self.generate_async(prompt))

    def generate_medgemma(
        self,
        prompt: str,
        temperature: float = 0.3,
        max_tokens: int = 256,
    ) -> str:
        """
        Sync generation matching VLLMModelManager.generate_medgemma() signature.
        Used by MedGemmaRunnable's `hasattr(agent, 'generate_medgemma')` path.
        """
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop and loop.is_running():
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as pool:
                future = pool.submit(
                    asyncio.run,
                    self.generate_async(prompt, temperature=temperature, max_tokens=max_tokens),
                )
                return future.result(timeout=self.timeout)
        else:
            return asyncio.run(
                self.generate_async(prompt, temperature=temperature, max_tokens=max_tokens)
            )

    def process_query(self, query: str, patient_context: dict | None = None) -> dict:
        """
        Sync interface matching MedGemmaAgent.process_query() signature.
        Used by scorer.py and debrief.py.
        """
        response = self.chat(query)
        return {"response": response}

    # ── Repr ──────────────────────────────────────────────────────────────────

    def __repr__(self) -> str:
        return f"VLLMClient(base_url={self.base_url!r}, model={self.model!r})"
