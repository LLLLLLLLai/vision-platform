from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import httpx
import numpy as np
from fastapi.testclient import TestClient
from PIL import Image

from app.services import paddle_ocr_client
from ocr_service import server


class OcrPreprocessingTests(unittest.IsolatedAsyncioTestCase):
    async def test_client_transmits_and_checks_both_switches(self):
        with TemporaryDirectory() as directory:
            source = Path(directory) / "image.png"
            Image.new("RGB", (20, 20)).save(source)
            for orientation, unwarping in [(True, False), (False, True), (True, True), (False, False)]:
                flags = {"use_orientation_classify": orientation, "use_doc_unwarping": unwarping}
                preprocessing = {**flags, "doc_angle_degrees": 90 if orientation else None, "coordinate_space": "PREPROCESSED_IMAGE" if orientation or unwarping else "INPUT_IMAGE"}

                def respond(request):
                    for key, value in flags.items():
                        self.assertIn(f'name="{key}"\r\n\r\n{str(value).lower()}'.encode(), request.content)
                    return httpx.Response(200, json={"status": "COMPLETED", "lines": [], "preprocessing": preprocessing})

                client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
                with patch.object(paddle_ocr_client.httpx, "AsyncClient", return_value=client):
                    result = await paddle_ocr_client.recognize_text(str(source), **flags)
                self.assertEqual(result["preprocessing"], preprocessing)

    async def test_old_service_cannot_silently_ignore_enabled_switch(self):
        with TemporaryDirectory() as directory:
            source = Path(directory) / "image.png"
            Image.new("RGB", (20, 20)).save(source)
            client = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"status": "COMPLETED", "lines": []})))
            with patch.object(paddle_ocr_client.httpx, "AsyncClient", return_value=client), self.assertRaisesRegex(paddle_ocr_client.OcrRequestError, "未确认"):
                await paddle_ocr_client.recognize_text(str(source), use_orientation_classify=True)

    def test_cached_engine_never_turns_other_nodes_flags_on(self):
        predict = Mock(return_value=[{"rec_texts": [], "rec_scores": [], "rec_polys": [], "doc_preprocessor_res": {"angle": 90}}])
        with patch.object(server, "_engine", SimpleNamespace(predict=predict)), patch.dict(server._engine_features, {"use_orientation_classify": True, "use_doc_unwarping": True}):
            for enabled in [True, False, True, False]:
                response = server.recognize_image(np.zeros((20, 20, 3)), use_orientation_classify=enabled, use_doc_unwarping=enabled)
                self.assertEqual(predict.call_args.kwargs["use_doc_orientation_classify"], enabled)
                self.assertEqual(predict.call_args.kwargs["use_doc_unwarping"], enabled)
                self.assertEqual(predict.call_args.kwargs["use_textline_orientation"], enabled)
                self.assertEqual(response["preprocessing"]["use_orientation_classify"], enabled)

    def test_missing_optional_model_keeps_previous_engine(self):
        previous = object()
        constructor = Mock()
        with patch.object(server, "_engine", previous), patch.dict(server._engine_features, {"use_orientation_classify": False, "use_doc_unwarping": False}), patch.dict("sys.modules", {"paddleocr": SimpleNamespace(PaddleOCR=constructor)}), patch.object(server, "_model_directory", side_effect=RuntimeError("missing")):
            with self.assertRaisesRegex(server.OcrModelConfigurationError, "未部署"):
                server._get_engine(use_doc_unwarping=True)
            self.assertIs(server._engine, previous)
            constructor.assert_not_called()
            self.assertFalse(server._engine_features["use_doc_unwarping"])

    def test_http_flags_and_missing_model_message(self):
        buffer = BytesIO()
        Image.new("RGB", (20, 20)).save(buffer, format="PNG")
        with patch.dict(server.os.environ, {"OCR_SERVICE_API_KEY": ""}), TestClient(server.app) as client:
            with patch.object(server, "recognize_image", return_value={"status": "COMPLETED", "lines": []}) as recognize:
                response = client.post("/ocr", files={"image": ("image.png", buffer.getvalue())}, data={"use_orientation_classify": "true", "use_doc_unwarping": "false"})
                self.assertEqual(response.status_code, 200)
                self.assertEqual(recognize.call_args.kwargs, {"use_orientation_classify": True, "use_doc_unwarping": False})
            with patch.object(server, "recognize_image", side_effect=server.OcrModelConfigurationError("UVDoc 未部署")):
                response = client.post("/ocr", files={"image": ("image.png", buffer.getvalue())})
                self.assertEqual(response.status_code, 503)
                self.assertIn("UVDoc 未部署", response.json()["detail"])
