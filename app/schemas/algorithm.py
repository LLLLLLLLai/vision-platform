from typing import Any

from pydantic import BaseModel, Field


class InferenceRequest(BaseModel):
    request_id: str
    capability: str
    engine: str | None = None
    image_path: str
    parameters: dict[str, Any] = Field(default_factory=dict)


class InferenceResponse(BaseModel):
    code: int = 0
    message: str = "success"
    request_id: str
    capability: str
    engine: str
    elapsed_ms: float
    result: dict[str, Any]

