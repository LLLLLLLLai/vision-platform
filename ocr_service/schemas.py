from pydantic import BaseModel, Field


class OcrRequest(BaseModel):
    image_path: str = Field(min_length=1)
    expected_text: str | None = Field(default=None, max_length=200)
