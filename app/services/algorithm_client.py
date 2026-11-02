from __future__ import annotations

from typing import Any


class LocalModelServicesRetiredError(RuntimeError):
    """Raised when legacy code tries to call a retired local model service."""


class AlgorithmServiceClient:
    """Compatibility facade for retired local DINO/OCR/Qwen services.

    Production detection must bind every ROI to a published scene. VLM scenes
    use ``openai_compatible_vlm`` directly, and trained YOLO models run through
    the scene runtime; neither depends on this legacy client.
    """

    @staticmethod
    def _retired(capability: str) -> LocalModelServicesRetiredError:
        return LocalModelServicesRetiredError(
            f"{capability}本地服务已下线。请将 ROI 绑定到已发布检测场景；"
            "VLM 场景请在“VLM 模型配置”中选择 OpenAI 兼容模型。"
        )

    async def health(self, service_url: str) -> dict[str, Any]:
        del service_url
        raise self._retired("本地模型")

    async def embedding(self, image_path: str) -> dict[str, Any]:
        del image_path
        raise self._retired("DINOv2")

    async def similarity(
        self,
        image_path: str,
        reference_paths: list[str],
        top_k: int = 3,
    ) -> dict[str, Any]:
        del image_path, reference_paths, top_k
        raise self._retired("DINOv2")

    async def vlm_judge(
        self,
        image_path: str,
        prompt: str,
        expected: dict[str, Any] | None = None,
        max_new_tokens: int = 160,
    ) -> dict[str, Any]:
        del image_path, prompt, expected, max_new_tokens
        raise self._retired("Qwen3-VL")

    async def vlm_compare(
        self,
        baseline_image_path: str,
        candidate_image_path: str,
        prompt: str,
        expected: dict[str, Any] | None = None,
        max_new_tokens: int = 220,
    ) -> dict[str, Any]:
        del baseline_image_path, candidate_image_path, prompt, expected, max_new_tokens
        raise self._retired("Qwen3-VL")

    async def inventory_objects(
        self,
        image_path: str,
        object_types: list[str] | None = None,
        max_types: int = 12,
    ) -> dict[str, Any]:
        del image_path, object_types, max_types
        raise self._retired("Qwen3-VL")

    async def ocr(
        self,
        image_path: str,
        expected_text: str | None = None,
    ) -> dict[str, Any]:
        del image_path, expected_text
        raise self._retired("PaddleOCR")
