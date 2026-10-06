"""
vLLM client — async OpenAI-compatible interface for MedGemma inference.

Supports three modes:
  - **local**: medgemma-4b-it on RTX 5060 (8GB VRAM), vLLM server on :8001
  - **cloud**: medgemma-27b-it (AWQ) on RunPod/Modal, remote endpoint
  - **modal**: calls VLLMService Modal class via RPC (no HTTP, no subprocess)

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

# ── Injection point for Modal RPC mode ───────────────────────────────────────
# In modal_app.py's serve(), set this to a ModalVLLMClient before importing main.
_injected_client: "VLLMClient | None" = None

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
    def from_env(cls) -> VLLMClient | ModalVLLMClient:
        """
        Build a VLLMClient from environment variables.

        Env vars:
          VLLM_MODE        = local | cloud | modal   (default: local)
          VLLM_LOCAL_URL    = http://localhost:8001/v1
          VLLM_CLOUD_URL    = https://your-pod.runpod.ai/v1
          VLLM_CLOUD_API_KEY = rp_xxx
          VLLM_MODEL        = override model name
        """
        mode = os.getenv("VLLM_MODE", "local").lower()

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
                raise ValueError("VLLM_MODE=cloud requires VLLM_CLOUD_URL")
            logger.info("VLLM_MODE=cloud — connecting to %s model=%s", base_url, model)

        elif mode == "modal":
            if _injected_client is not None:
                logger.info("VLLM_MODE=modal — using injected ModalVLLMClient (Modal RPC)")
                return _injected_client
            raise RuntimeError("VLLM_MODE=modal requires an injected ModalVLLMClient")

        else:
            raise ValueError("Unknown VLLM_MODE=%s. Choose local, cloud, or modal." % mode)

        return cls(base_url=base_url, api_key=api_key, model=model)

    # ── Async interface ───────────────────────────────────────────────────────

    async def chat_async(
        self,
        messages: list[dict[str, Any]],
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
        messages: list[dict[str, Any]],
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
        max_tokens: int = 1024,
    ) -> str:
        """
        Sync generation matching VLLMModelManager.generate_medgemma() signature.
        Used by MedGemmaRunnable's `hasattr(agent, 'generate_medgemma')` path.

        Default max_tokens increased to 1024 for long-form outputs like debriefs.
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

    def process_query(self, query: str, patient_context: dict | None = None, max_tokens: int = 1024) -> dict:
        """
        Sync interface matching MedGemmaAgent.process_query() signature.
        Used by scorer.py and debrief.py.

        Parameters
        ----------
        max_tokens : int
            Maximum tokens to generate. Default 1024 for debrief/scoring prompts.
            Use 256 for shorter responses.
        """
        response = self.generate_medgemma(query, max_tokens=max_tokens)
        return {"response": response}

    # ── Repr ──────────────────────────────────────────────────────────────────

    def __repr__(self) -> str:
        return f"VLLMClient(base_url={self.base_url!r}, model={self.model!r})"


# ── Modal RPC client ──────────────────────────────────────────────────────────

class ModalVLLMClient:
    """
    Duck-type replacement for VLLMClient that routes inference requests to a
    VLLMService Modal class via Modal's RPC system instead of HTTP.

    Injected by modal_app.py's serve() into vllm_client._injected_client
    so that main.py's VLLMClient.from_env() picks it up when VLLM_MODE=modal.
    """

    def __init__(self, vllm_service_instance: Any, model: str = _DEFAULT_LOCAL_MODEL) -> None:
        self._svc = vllm_service_instance
        self.model = model
        self.base_url = "modal-rpc"   # For repr / health logging only

    # ── Async interface ───────────────────────────────────────────────────────

    async def chat_async(
        self,
        messages: list[dict[str, Any]],
        temperature: float = 0.7,
        max_tokens: int = 256,
        **kwargs,
    ) -> str:
        """Call VLLMService.generate.aio() via Modal RPC.

        Uses the full conversation history - the model's chat template
        handles proper formatting internally.
        """
        logger.info("ModalVLLMClient: dispatching chat with %d messages", len(messages))
        return await self._svc.chat.remote.aio(
            messages, max_tokens=max_tokens, temperature=temperature
        )

    async def generate_async(
        self,
        prompt: str,
        temperature: float = 0.7,
        max_tokens: int = 1024,
        **kwargs,
    ) -> str:
        """Generate completion for a prompt.

        Default max_tokens increased to 1024 for long-form outputs like debriefs.
        """
        return await self._svc.generate.remote.aio(
            prompt, max_tokens=max_tokens, temperature=temperature
        )

    async def health_async(self) -> bool:
        """VLLMService is healthy if Modal routing reached us."""
        try:
            result = await self._svc.health.remote.aio()
            return result.get("status") == "ok"
        except Exception:
            return True  # Assume healthy — health check failure shouldn't block startup

    # ── Sync interface ────────────────────────────────────────────────────────

    def _run_sync(self, coro):
        """Run an async coroutine from a sync context, thread-safely."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop and loop.is_running():
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as pool:
                future = pool.submit(asyncio.run, coro)
                return future.result(timeout=120)
        else:
            return asyncio.run(coro)

    def chat(self, prompt: str) -> str:
        return self._run_sync(self.generate_async(prompt))

    def sync_chat_messages(
        self,
        messages: list[dict[str, Any]],
        temperature: float = 0.7,
        max_tokens: int = 512,
    ) -> str:
        return self._run_sync(self.chat_async(messages, temperature=temperature, max_tokens=max_tokens))

    def generate_medgemma(
        self,
        prompt: str,
        temperature: float = 0.3,
        max_tokens: int = 1024,
    ) -> str:
        """Generate response for MedGemma prompts (scoring, debrief, case generation).

        Default max_tokens increased to 1024 to support long-form outputs like
        clinical debriefs which require 8 sections with 2-3 sentences each.
        """
        return self._run_sync(self.generate_async(prompt, temperature=temperature, max_tokens=max_tokens))

    def process_query(self, query: str, patient_context: dict | None = None, max_tokens: int = 1024) -> dict:
        """
        Sync interface matching MedGemmaAgent.process_query() signature.
        Used by scorer.py and debrief.py.

        Parameters
        ----------
        max_tokens : int
            Maximum tokens to generate. Default 1024 for debrief/scoring prompts.
            Use 256 for shorter responses.
        """
        return {"response": self.generate_medgemma(query, max_tokens=max_tokens)}

    def __repr__(self) -> str:
        return f"ModalVLLMClient(model={self.model!r})"
