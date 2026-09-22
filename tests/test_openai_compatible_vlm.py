import base64
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from PIL import Image
import httpx

from app.core.config import settings
from app.services.openai_compatible_vlm import _api_root, _http_error_message, _image_content


class OpenAiCompatibleVlmUrlTests(unittest.TestCase):
    def test_accepts_service_root_and_v1_root(self) -> None:
        self.assertEqual(_api_root("http://127.0.0.1:8000"), "http://127.0.0.1:8000/v1")
        self.assertEqual(_api_root("http://127.0.0.1:8000/v1"), "http://127.0.0.1:8000/v1")

    def test_normalizes_copied_openai_endpoints(self) -> None:
        self.assertEqual(
            _api_root("http://127.0.0.1:8000/v1/models"),
            "http://127.0.0.1:8000/v1",
        )
        self.assertEqual(
            _api_root("http://127.0.0.1:8000/v1/chat/completions"),
            "http://127.0.0.1:8000/v1",
        )

    def test_large_image_is_downscaled_before_vlm_request(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "large.png"
            Image.new("RGB", (3200, 2400), "white").save(path)

            content = _image_content(str(path))

        image_url = content["image_url"]["url"]
        header, encoded = image_url.split(",", 1)
        self.assertEqual(header, "data:image/jpeg;base64")
        with Image.open(BytesIO(base64.b64decode(encoded))) as image:
            self.assertLessEqual(image.width * image.height, settings.vlm_request_max_image_pixels)

    def test_http_error_exposes_provider_message_without_request_data(self) -> None:
        response = httpx.Response(
            400,
            json={"error": {"message": "Input length exceeds model context."}},
        )

        message = _http_error_message(response)

        self.assertEqual(message, "VLM 服务返回 HTTP 400：Input length exceeds model context.")
