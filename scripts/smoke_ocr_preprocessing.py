from io import BytesIO
import json
import sys
import time

import httpx
from PIL import Image, ImageDraw, ImageFont


base_url = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:9024"
image = Image.new("RGB", (640, 200), "white")
ImageDraw.Draw(image).text((30, 45), "FU5 400A", fill="black", font=ImageFont.load_default(size=65))
with httpx.Client(timeout=180, trust_env=False) as client:
    for orientation, unwarping, angle in [(False, False, 0), (True, False, 90), (False, True, 0), (True, True, 90), (False, False, 0)]:
        stream = BytesIO()
        image.rotate(angle, expand=True).save(stream, format="PNG")
        started = time.perf_counter()
        flags = {"use_orientation_classify": orientation, "use_doc_unwarping": unwarping}
        response = client.post(f"{base_url}/ocr", files={"image": ("synthetic.png", stream.getvalue(), "image/png")}, data={key: str(value).lower() for key, value in flags.items()})
        payload = response.json()
        assert response.status_code == 200, payload
        assert payload["status"] == "COMPLETED", payload
        assert all(payload["preprocessing"][key] == value for key, value in flags.items()), payload
        print(json.dumps({"requested": flags, "rotated_input": angle, "preprocessing": payload["preprocessing"], "text": [line["text"] for line in payload["lines"]], "elapsed_seconds": round(time.perf_counter() - started, 2)}, ensure_ascii=False), flush=True)
