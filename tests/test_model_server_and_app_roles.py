from __future__ import annotations

import asyncio
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import unittest

from fastapi.testclient import TestClient
import httpx
from PIL import Image

from app.core.config import settings
from app.inference_server import main as inference_main
from app.main import create_app
from app.services import remote_yolo_inference
from app.services.vision_model_runtime import PublishedVisionModelSpec


class ModelServerAndAppRoleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.original_storage_root = settings.inference_model_storage_root
        self.original_api_key = settings.inference_api_key
        self.original_yolo_mode = settings.yolo_inference_mode
        self.original_yolo_base_url = settings.yolo_inference_base_url
        self.original_yolo_api_key = settings.yolo_inference_api_key
        self.original_model_root = settings.vision_model_storage_root
        settings.inference_model_storage_root = str(self.root / "models")
        settings.inference_api_key = "server-secret"
        weights = Path(settings.inference_model_storage_root) / "published" / "best.pt"
        weights.parent.mkdir(parents=True, exist_ok=True)
        weights.write_bytes(b"fake weights")
        self.image = self.root / "input.jpg"
        Image.new("RGB", (20, 20), "white").save(self.image)

    def tearDown(self) -> None:
        settings.inference_model_storage_root = self.original_storage_root
        settings.inference_api_key = self.original_api_key
        settings.yolo_inference_mode = self.original_yolo_mode
        settings.yolo_inference_base_url = self.original_yolo_base_url
        settings.yolo_inference_api_key = self.original_yolo_api_key
        settings.vision_model_storage_root = self.original_model_root
        self.temporary_directory.cleanup()

    def test_model_server_requires_key_and_runs_with_relative_model_key(self) -> None:
        expected = {
            "result": "OK",
            "confidence": 0.95,
            "task_type": "YOLO_DETECTION",
            "objects": [],
            "image": {"width": 20, "height": 20},
        }
        with TestClient(inference_main.app) as client, patch.object(
            inference_main,
            "run_yolo_inference",
            return_value=dict(expected),
        ) as inference:
            response = client.post(
                "/v1/yolo/infer",
                headers={"X-Model-API-Key": "server-secret"},
                data={
                    "model_key": "published/best.pt",
                    "task_type": "YOLO_DETECTION",
                    "labels_json": '["harness"]',
                    "config_json": '{"confidence": 0.2}',
                },
                files={"image": ("input.jpg", self.image.read_bytes(), "image/jpeg")},
            )
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()["runtime"], "MODEL_SERVER")
            inference.assert_called_once()

            unauthorized = client.post(
                "/v1/yolo/infer",
                data={"model_key": "published/best.pt", "task_type": "YOLO_DETECTION"},
                files={"image": ("input.jpg", self.image.read_bytes(), "image/jpeg")},
            )
            self.assertEqual(unauthorized.status_code, 401)

    def test_model_server_rejects_path_traversal(self) -> None:
        with TestClient(inference_main.app) as client:
            response = client.post(
                "/v1/yolo/infer",
                headers={"X-Model-API-Key": "server-secret"},
                data={"model_key": "../secret.pt", "task_type": "YOLO_DETECTION"},
                files={"image": ("input.jpg", self.image.read_bytes(), "image/jpeg")},
            )
        self.assertEqual(response.status_code, 422)

    def test_application_roles_expose_only_the_expected_routes(self) -> None:
        control_paths = {route.path for route in create_app("CONTROL").routes}
        inspection_paths = {route.path for route in create_app("INSPECTION").routes}
        self.assertIn("/api/v1/scenarios", control_paths)
        self.assertNotIn("/api/detect", control_paths)
        self.assertIn("/api/detect", inspection_paths)
        self.assertIn("/api/v1/scenarios/invoke/{scene_code}", inspection_paths)
        self.assertNotIn("/api/v1/scenarios", inspection_paths)

    def test_application_remote_client_uses_relative_model_key(self) -> None:
        settings.vision_model_storage_root = str(self.root / "models")
        settings.yolo_inference_mode = "REMOTE"
        settings.yolo_inference_base_url = "http://gpu-160:8091"
        settings.yolo_inference_api_key = "application-secret"
        weights = Path(settings.vision_model_storage_root) / "published" / "best.pt"
        spec = PublishedVisionModelSpec(
            version_id=8,
            model_id=3,
            model_code="AI_HARNESS",
            model_name="AI 检测线束",
            task_type="YOLO_DETECTION",
            labels=("harness",),
            weights_path=weights,
        )
        captured: dict[str, object] = {}

        class FakeAsyncClient:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                return False

            async def post(self, url, *, headers, data, files):
                captured.update({"url": url, "headers": headers, "data": data})
                return httpx.Response(
                    200,
                    request=httpx.Request("POST", url),
                    json={
                        "result": "OK",
                        "image": {"path": "/tmp/staged.jpg", "width": 20, "height": 20},
                        "objects": [],
                    },
                )

        with patch.object(remote_yolo_inference.httpx, "AsyncClient", return_value=FakeAsyncClient()):
            output = asyncio.run(
                remote_yolo_inference.run_remote_yolo_inference(
                    spec,
                    str(self.image),
                    config={"expected_min_count": 1},
                )
            )

        self.assertEqual(captured["url"], "http://gpu-160:8091/v1/yolo/infer")
        self.assertEqual(captured["headers"], {"X-Model-API-Key": "application-secret"})
        self.assertEqual(captured["data"]["model_key"], "published/best.pt")
        self.assertEqual(output["runtime"], "REMOTE")
        self.assertEqual(output["image"]["path"], str(self.image))
