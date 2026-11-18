import asyncio
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
import threading
import time
import unittest
from unittest.mock import patch

import httpx
from fastapi import UploadFile
from PIL import Image

from app.services import paddle_ocr_client as client_module
from ocr_service import server


class OcrConcurrencyTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        buffer = BytesIO()
        Image.new("RGB", (20, 20)).save(buffer, "PNG")
        self.data = buffer.getvalue()
        self.admission = threading.BoundedSemaphore(32)
        self.execution = threading.BoundedSemaphore(1)
        self.patches = [patch.object(server, "_admission", self.admission),
                        patch.object(server, "_execution", self.execution),
                        patch.object(server, "_queue_wait_seconds", 2),
                        patch.dict(server.os.environ, {"OCR_SERVICE_API_KEY": ""})]
        for patcher in self.patches:
            patcher.start()
            self.addCleanup(patcher.stop)

    async def request(self):
        return await server.ocr(UploadFile(filename="test.png", file=BytesIO(self.data)), "", False, False)

    async def test_twenty_parallel_rois_queue_without_429_or_parallel_engine_access(self):
        active = 0
        peak = 0

        def recognize(*args, **kwargs):
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            time.sleep(0.01)
            active -= 1
            return {"status": "COMPLETED", "lines": []}

        with patch.object(server, "recognize_image", side_effect=recognize):
            results = await asyncio.gather(*(self.request() for index in range(20)))
        self.assertEqual(len(results), 20)
        self.assertEqual(peak, 1)
        self.assertTrue(self.execution.acquire(False))
        self.execution.release()

    async def test_overflow_and_wait_timeout_are_bounded_and_retryable(self):
        transport = httpx.ASGITransport(app=server.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://ocr") as client:
            with patch.object(server, "_admission", threading.BoundedSemaphore(0)):
                response = await client.post("/ocr", files={"image": ("a.png", self.data)})
            self.assertEqual(response.status_code, 429)
            self.assertEqual(response.headers["Retry-After"], "1")
            self.execution.acquire()
            with patch.object(server, "_queue_wait_seconds", 0.01):
                response = await client.post("/ocr", files={"image": ("a.png", self.data)})
            self.execution.release()
            self.assertEqual(response.status_code, 429)
            self.assertIn("timed out", response.json()["detail"])
            for index in range(32):
                self.assertTrue(self.admission.acquire(False))
            self.assertFalse(self.admission.acquire(False))

    async def test_canceled_request_does_not_release_running_engine_early(self):
        started = threading.Event()
        finish = threading.Event()

        def recognize(*args, **kwargs):
            started.set()
            finish.wait(2)
            return {"status": "COMPLETED", "lines": []}

        with patch.object(server, "recognize_image", side_effect=recognize):
            request = asyncio.create_task(self.request())
            while not started.is_set():
                await asyncio.sleep(0.005)
            request.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await request
            try:
                self.assertFalse(self.execution.acquire(False))
            finally:
                finish.set()
                await asyncio.gather(*list(server._inflight))
        self.assertTrue(self.execution.acquire(False))
        self.execution.release()

    async def test_client_retries_only_busy_and_resends_entire_image(self):
        with TemporaryDirectory() as directory:
            source = Path(directory) / "sample.png"
            source.write_bytes(self.data)
            for statuses in ([429, 429, 200], [429, 429, 429], [503]):
                calls = []

                def respond(request):
                    self.assertIn(self.data, request.content)
                    status = statuses[len(calls)]
                    calls.append(status)
                    return httpx.Response(status, headers={"Retry-After": "0"},
                                          json={"status": "COMPLETED", "lines": [], "detail": "busy"})

                client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
                with patch.object(client_module.httpx, "AsyncClient", return_value=client):
                    if statuses[-1] == 200:
                        self.assertEqual((await client_module.recognize_text(str(source)))["text_count"], 0)
                    else:
                        with self.assertRaises(client_module.OcrRequestError):
                            await client_module.recognize_text(str(source))
                self.assertEqual(calls, statuses)

    async def test_client_deadline_includes_retry_wait(self):
        with TemporaryDirectory() as directory:
            source = Path(directory) / "sample.png"
            source.write_bytes(self.data)
            client = httpx.AsyncClient(transport=httpx.MockTransport(
                lambda request: httpx.Response(429, headers={"Retry-After": "5"}, json={"detail": "busy"})))
            with patch.object(client_module.httpx, "AsyncClient", return_value=client), patch.object(client_module.settings, "ocr_service_timeout_seconds", 0.03):
                with self.assertRaisesRegex(client_module.OcrRequestError, "超时"):
                    await client_module.recognize_text(str(source))
