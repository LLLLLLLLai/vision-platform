from pydantic import BaseModel, Field


class OcrRequest(BaseModel):
    image_path: str = Field(min_length=1)
