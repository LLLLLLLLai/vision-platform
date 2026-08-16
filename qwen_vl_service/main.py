import asyncio
import json
import math
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import torch
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from PIL import Image
from pydantic import BaseModel, Field
from transformers import (
    AutoModelForImageTextToText,
    AutoProcessor,
    BitsAndBytesConfig,
)

from qwen_vl_service.utils import parse_json_object, parse_partial_object_list


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


class Settings:
    model_id = os.getenv("QWEN_VL_MODEL_ID", "Qwen/Qwen3-VL-4B-Instruct")
    model_path = Path(
        os.getenv(
            "QWEN_VL_MODEL_PATH",
            str(PROJECT_ROOT / "vision-models" / "qwen3-vl-4b-instruct"),
        )
    )
    device = os.getenv("QWEN_VL_DEVICE", "auto").lower()
    quantization = os.getenv("QWEN_VL_QUANTIZATION", "4bit").lower()
    load_on_startup = env_bool("QWEN_VL_LOAD_ON_STARTUP", True)
    local_files_only = env_bool("QWEN_VL_LOCAL_FILES_ONLY", True)
    max_new_tokens = int(os.getenv("QWEN_VL_MAX_NEW_TOKENS", "1024"))
    max_image_pixels = int(os.getenv("QWEN_VL_MAX_IMAGE_PIXELS", "1003520"))
    gpu_memory_gib = int(os.getenv("QWEN_VL_GPU_MEMORY_GIB", "6"))


settings = Settings()


class JudgeRequest(BaseModel):
    image_path: str
    prompt: str = Field(min_length=1, max_length=4000)
    expected: dict[str, Any] = Field(default_factory=dict)
    system_prompt: str = (
        "You are an industrial visual inspection assistant. Judge only visible "
        "evidence in the supplied ROI image. Never guess. If evidence is unclear, "
        "return UNCERTAIN. Return one JSON object and no markdown."
    )
    max_new_tokens: int = Field(default=160, ge=16, le=512)


class CompareRequest(BaseModel):
    baseline_image_path: str
    candidate_image_path: str
    prompt: str = Field(min_length=1, max_length=4000)
    expected: dict[str, Any] = Field(default_factory=dict)
    system_prompt: str = (
        "You are an industrial visual inspection reviewer. Image 1 is the approved "
        "baseline ROI and image 2 is a production candidate ROI. Compare only the "
        "requested component. Never guess. Return UNCERTAIN when evidence is unclear. "
        "Return one JSON object and no markdown."
    )
    max_new_tokens: int = Field(default=220, ge=32, le=512)


class DiscoverRequest(BaseModel):
    image_path: str
    object_types: list[str] = Field(default_factory=list, max_length=30)
    max_objects: int = Field(default=30, ge=1, le=80)
    max_new_tokens: int = Field(default=1024, ge=64, le=1024)


class InventoryRequest(BaseModel):
    image_path: str
    object_types: list[str] = Field(default_factory=list, max_length=30)
    max_types: int = Field(default=12, ge=1, le=30)
    max_new_tokens: int = Field(default=320, ge=64, le=512)


class QwenVlEngine:
    def __init__(self) -> None:
        self.model: Any = None
        self.processor: Any = None
        self.status = "NOT_LOADED"
        self.error: str | None = None
        self.loaded_from: str | None = None

    def load(self) -> None:
        if self.model is not None:
            return
        self.status = "LOADING"
        self.error = None
        source = str(settings.model_path) if settings.model_path.exists() else settings.model_id
        if settings.local_files_only and not settings.model_path.exists():
            self.status = "ERROR"
            self.error = f"Local model directory does not exist: {settings.model_path}"
            raise FileNotFoundError(self.error)

        load_kwargs: dict[str, Any] = {
            "device_map": "auto" if settings.device == "auto" else settings.device,
            "local_files_only": settings.local_files_only,
            "low_cpu_mem_usage": True,
        }
        if torch.cuda.is_available():
            load_kwargs["dtype"] = torch.float16
            load_kwargs["max_memory"] = {
                0: f"{settings.gpu_memory_gib}GiB",
                "cpu": "24GiB",
            }
            if settings.quantization == "4bit":
                load_kwargs["quantization_config"] = BitsAndBytesConfig(
                    load_in_4bit=True,
                    bnb_4bit_quant_type="nf4",
                    bnb_4bit_compute_dtype=torch.float16,
                    bnb_4bit_use_double_quant=True,
                )
        else:
            load_kwargs["dtype"] = torch.float32
            load_kwargs["device_map"] = "cpu"

        try:
            self.processor = AutoProcessor.from_pretrained(
                source,
                local_files_only=settings.local_files_only,
            )
            self.model = AutoModelForImageTextToText.from_pretrained(
                source,
                **load_kwargs,
            )
            self.model.eval()
            self.loaded_from = source
            self.status = "READY"
        except Exception as exc:
            self.model = None
            self.processor = None
            self.status = "ERROR"
            self.error = str(exc)
            raise

    def _resize_image(self, image: Image.Image) -> Image.Image:
        pixel_count = image.width * image.height
        if pixel_count <= settings.max_image_pixels:
            return image
        scale = math.sqrt(settings.max_image_pixels / pixel_count)
        size = (
            max(32, int(image.width * scale)),
            max(32, int(image.height * scale)),
        )
        return image.resize(size, Image.Resampling.LANCZOS)

    def _load_image(self, image_path: str) -> Image.Image:
        image_file = Path(image_path)
        if not image_file.is_file():
            raise FileNotFoundError(f"Image does not exist: {image_file}")

        with Image.open(image_file) as source_image:
            return self._resize_image(source_image.convert("RGB"))

    def _generate_json(
        self,
        image: Image.Image | list[Image.Image],
        system_prompt: str,
        user_prompt: str,
        max_new_tokens: int,
    ) -> dict[str, Any]:
        images = image if isinstance(image, list) else [image]
        messages = [
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": [
                    *[
                        {"type": "image", "image": current_image}
                        for current_image in images
                    ],
                    {"type": "text", "text": user_prompt},
                ],
            },
        ]
        inputs = self.processor.apply_chat_template(
            messages,
            tokenize=True,
            add_generation_prompt=True,
            return_dict=True,
            return_tensors="pt",
        )
        inputs = inputs.to(self.model.device)
        with torch.inference_mode():
            generated_ids = self.model.generate(
                **inputs,
                max_new_tokens=min(max_new_tokens, settings.max_new_tokens),
                do_sample=False,
            )
        trimmed_ids = [
            output_ids[len(input_ids) :]
            for input_ids, output_ids in zip(inputs.input_ids, generated_ids)
        ]
        raw_text = self.processor.batch_decode(
            trimmed_ids,
            skip_special_tokens=True,
            clean_up_tokenization_spaces=False,
        )[0].strip()
        return {
            "raw_text": raw_text,
            "parsed": parse_json_object(raw_text),
            "input_width": images[0].width,
            "input_height": images[0].height,
            "input_images": [
                {"width": current_image.width, "height": current_image.height}
                for current_image in images
            ],
        }

    def judge(self, request: JudgeRequest) -> dict[str, Any]:
        self.load()
        image = self._load_image(request.image_path)

        expected_text = json.dumps(request.expected, ensure_ascii=False)
        user_prompt = (
            f"Inspection requirement: {request.prompt}\n"
            f"Expected state: {expected_text}\n"
            "Judge only the requested inspection item. Keep actual concise and "
            "keep reason under 80 characters. Do not list unrelated objects.\n"
            "Return exactly this JSON schema: "
            '{"result":"OK|NG|UNCERTAIN|ERROR","actual":{},'
            '"confidence":0.0,"reason":""}'
        )
        return self._generate_json(
            image,
            request.system_prompt,
            user_prompt,
            request.max_new_tokens,
        )

    def compare(self, request: CompareRequest) -> dict[str, Any]:
        self.load()
        baseline = self._load_image(request.baseline_image_path)
        candidate = self._load_image(request.candidate_image_path)
        expected_text = json.dumps(request.expected, ensure_ascii=False)
        user_prompt = (
            "Image 1 is the approved baseline ROI. Image 2 is the production candidate ROI.\n"
            f"Inspection requirements: {request.prompt}\n"
            f"Expected state and hard-rule evidence: {expected_text}\n"
            "Compare object identity, presence, visible model appearance, installation "
            "state and image quality. Ignore harmless lighting or tiny crop differences. "
            "If any critical difference is visible, return REJECT. If the two images "
            "cannot be compared reliably, return UNCERTAIN.\n"
            "Return exactly this JSON schema: "
            '{"result":"PASS|REJECT|UNCERTAIN","same_object":true,'
            '"object_present":true,"appearance_consistent":true,'
            '"installation_consistent":true,"critical_difference":false,'
            '"image_quality_ok":true,"confidence":0.0,"differences":[],"reason":""}'
        )
        return self._generate_json(
            [baseline, candidate],
            request.system_prompt,
            user_prompt,
            request.max_new_tokens,
        )

    def discover(self, request: DiscoverRequest) -> dict[str, Any]:
        self.load()
        image = self._load_image(request.image_path)
        requested_types = ", ".join(request.object_types) or (
            "fuse, screw, connector, wiring harness connection, PCBA, busbar, "
            "relay, terminal, label and other independently inspectable parts"
        )
        system_prompt = (
            "You are an industrial vision scene parser. Find visible, independently "
            "inspectable assembly objects. Do not guess hidden objects. Return one "
            "JSON object only, without markdown."
        )
        user_prompt = (
            f"Discover at most {request.max_objects} inspection objects in this image. "
            f"Prioritize these types: {requested_types}. Repeated parts must be listed "
            "separately. Avoid one large box around the whole product when smaller "
            "inspectable parts are visible. Use coordinates on a 0-1000 scale. "
            "Return exactly: "
            '{"objects":[{"label":"Chinese concise name","object_type":'
            '"FUSE|SCREW|CONNECTOR|HARNESS|PCBA|BUSBAR|LABEL|RELAY|TERMINAL|OBJECT",'
            '"prompt_en":"short English visual phrase","bbox":[x1,y1,x2,y2],'
            '"confidence":0.0}]}'
        )
        result = self._generate_json(
            image,
            system_prompt,
            user_prompt,
            request.max_new_tokens,
        )
        if result["parsed"] is None:
            partial_objects = parse_partial_object_list(result["raw_text"])
            if partial_objects:
                result["parsed"] = {"objects": partial_objects, "partial": True}
        return result

    def inventory(self, request: InventoryRequest) -> dict[str, Any]:
        self.load()
        image = self._load_image(request.image_path)
        requested_types = ", ".join(request.object_types) or (
            "fuse, screw, connector, wiring harness connection, PCBA, busbar, "
            "relay, terminal and label"
        )
        system_prompt = (
            "You are an industrial assembly scene analyst. Identify only object types "
            "that are visibly present in the image. Do not output coordinates. Never "
            "invent hidden objects, never copy schema placeholders, and never combine "
            "multiple type names into one item. Return one compact JSON object only."
        )
        user_prompt = (
            f"List at most {request.max_types} visible, independently inspectable "
            f"object types. Prioritize: {requested_types}. Merge repeated parts into "
            "one type and estimate the visible count. Use a short concrete English "
            "phrase suitable for an open-vocabulary detector. object_type must be one "
            "single value from FUSE, SCREW, CONNECTOR, HARNESS, PCBA, BUSBAR, LABEL, "
            "RELAY, TERMINAL, OBJECT. For example, an image containing four screws and "
            "two connectors would return "
            '{"objects":[{"label":"螺丝","object_type":"SCREW",'
            '"prompt_en":"metal screw","expected_count":4},{"label":"连接器",'
            '"object_type":"CONNECTOR","prompt_en":"electrical connector",'
            '"expected_count":2}]}. Analyze the current image and return only its actual objects.'
        )
        result = self._generate_json(
            image,
            system_prompt,
            user_prompt,
            request.max_new_tokens,
        )
        parsed = result.get("parsed")
        if isinstance(parsed, dict):
            objects = parsed.get("objects")
            if isinstance(objects, list):
                parsed["objects"] = [
                    item
                    for item in objects
                    if isinstance(item, dict)
                    and item.get("object_type") in {
                        "FUSE",
                        "SCREW",
                        "CONNECTOR",
                        "HARNESS",
                        "PCBA",
                        "BUSBAR",
                        "LABEL",
                        "RELAY",
                        "TERMINAL",
                        "OBJECT",
                    }
                    and isinstance(item.get("prompt_en"), str)
                    and "," not in item["prompt_en"]
                    and "|" not in item["prompt_en"]
                ]
        return result


engine = QwenVlEngine()
inference_lock = asyncio.Lock()


@asynccontextmanager
async def lifespan(_: FastAPI):
    if settings.load_on_startup:
        try:
            await asyncio.to_thread(engine.load)
        except Exception:
            pass
    yield


app = FastAPI(
    title="Qwen3-VL Industrial Review Service",
    version="0.1.0",
    lifespan=lifespan,
)


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    status_color = "#16a34a" if engine.status == "READY" else "#d97706"
    return f"""
    <!doctype html>
    <html lang="zh-CN">
      <head>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <title>Qwen3-VL 工业视觉复核服务</title>
        <style>
          body {{ margin: 0; font-family: system-ui, sans-serif; background: #f4f7fb; color: #172033; }}
          main {{ max-width: 760px; margin: 8vh auto; padding: 0 24px; }}
          section {{ background: white; border-radius: 18px; padding: 32px; box-shadow: 0 14px 40px #23304d18; }}
          h1 {{ margin: 0 0 8px; font-size: 28px; }}
          p {{ color: #5b6475; line-height: 1.7; }}
          .status {{ display: inline-flex; gap: 8px; align-items: center; padding: 8px 12px; background: {status_color}15; color: {status_color}; border-radius: 999px; font-weight: 700; }}
          .dot {{ width: 9px; height: 9px; background: {status_color}; border-radius: 50%; }}
          dl {{ display: grid; grid-template-columns: 130px 1fr; gap: 12px; margin: 28px 0; }}
          dt {{ color: #737b8c; }} dd {{ margin: 0; font-weight: 600; word-break: break-all; }}
          a {{ display: inline-block; margin-right: 10px; padding: 10px 16px; border-radius: 10px; text-decoration: none; background: #2563eb; color: white; }}
          a.secondary {{ background: #e8eefc; color: #1d4ed8; }}
        </style>
      </head>
      <body>
        <main><section>
          <div class="status"><span class="dot"></span>{engine.status}</div>
          <h1>Qwen3-VL 工业视觉复核服务</h1>
          <p>用于错、漏、混场景中的复杂视觉判断和低置信度复核。</p>
          <dl>
            <dt>模型</dt><dd>{settings.model_id}</dd>
            <dt>量化方式</dt><dd>{settings.quantization}</dd>
            <dt>运行设备</dt><dd>{"CUDA GPU" if torch.cuda.is_available() else "CPU"}</dd>
            <dt>服务端口</dt><dd>9023</dd>
          </dl>
          <a href="/docs">打开接口文档</a>
          <a class="secondary" href="/health">查看健康状态</a>
        </section></main>
      </body>
    </html>
    """


@app.get("/health")
def health() -> dict[str, Any]:
    gpu_memory = None
    if torch.cuda.is_available():
        gpu_memory = {
            "allocated_mb": round(torch.cuda.memory_allocated() / 1024**2, 2),
            "reserved_mb": round(torch.cuda.memory_reserved() / 1024**2, 2),
            "free_mb": round(torch.cuda.mem_get_info()[0] / 1024**2, 2),
        }
    return {
        "status": engine.status,
        "model": settings.model_id,
        "loaded_from": engine.loaded_from,
        "quantization": settings.quantization,
        "device": "cuda" if torch.cuda.is_available() else "cpu",
        "gpu_memory": gpu_memory,
        "error": engine.error,
    }


@app.post("/v1/load")
async def load_model() -> dict[str, str]:
    try:
        await asyncio.to_thread(engine.load)
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {"status": engine.status, "model": settings.model_id}


@app.post("/v1/judge")
async def judge(request: JudgeRequest) -> dict[str, Any]:
    started = time.perf_counter()
    try:
        async with inference_lock:
            result = await asyncio.to_thread(engine.judge, request)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return {
        "code": 0,
        "message": "success",
        "model": settings.model_id,
        "elapsed_ms": round((time.perf_counter() - started) * 1000, 2),
        "result": result,
    }


@app.post("/v1/compare")
async def compare(request: CompareRequest) -> dict[str, Any]:
    started = time.perf_counter()
    try:
        async with inference_lock:
            result = await asyncio.to_thread(engine.compare, request)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return {
        "code": 0,
        "message": "success",
        "model": settings.model_id,
        "elapsed_ms": round((time.perf_counter() - started) * 1000, 2),
        "result": result,
    }


@app.post("/v1/discover")
async def discover(request: DiscoverRequest) -> dict[str, Any]:
    started = time.perf_counter()
    try:
        async with inference_lock:
            result = await asyncio.to_thread(engine.discover, request)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return {
        "code": 0,
        "message": "success",
        "model": settings.model_id,
        "elapsed_ms": round((time.perf_counter() - started) * 1000, 2),
        "result": result,
    }


@app.post("/v1/inventory")
async def inventory(request: InventoryRequest) -> dict[str, Any]:
    started = time.perf_counter()
    try:
        async with inference_lock:
            result = await asyncio.to_thread(engine.inventory, request)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return {
        "code": 0,
        "message": "success",
        "model": settings.model_id,
        "elapsed_ms": round((time.perf_counter() - started) * 1000, 2),
        "result": result,
    }
