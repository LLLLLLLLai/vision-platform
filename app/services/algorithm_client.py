from typing import Any

import httpx

from app.core.config import settings


class AlgorithmServiceClient:
    def __init__(self) -> None:
        self.timeout = settings.algorithm_timeout_seconds

    async def health(self, service_url: str) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.get(f"{service_url.rstrip('/')}/health")
            response.raise_for_status()
            return response.json()

    async def localize(
        self,
        image_path: str,
        prompts: list[str],
        threshold: float = 0.25,
    ) -> dict[str, Any]:
        payload = {
            "image_path": image_path,
            "prompts": prompts,
            "threshold": threshold,
        }
        return await self._post(
            settings.grounding_service_url,
            "/v1/localize",
            payload,
        )

    async def embedding(self, image_path: str) -> dict[str, Any]:
        return await self._post(
            settings.dinov2_service_url,
            "/v1/embedding",
            {"image_path": image_path},
        )

    async def similarity(
        self,
        image_path: str,
        reference_paths: list[str],
        top_k: int = 3,
    ) -> dict[str, Any]:
        payload = {
            "image_path": image_path,
            "reference_paths": reference_paths,
            "top_k": top_k,
        }
        return await self._post(
            settings.dinov2_service_url,
            "/v1/similarity",
            payload,
        )

    async def _post(
        self,
        base_url: str,
        path: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(
                f"{base_url.rstrip('/')}{path}",
                json=payload,
            )
            response.raise_for_status()
            return response.json()

