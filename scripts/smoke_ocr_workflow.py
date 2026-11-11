import asyncio
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx
from fastapi import FastAPI
from PIL import Image, ImageDraw, ImageFont
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.api.routes.scenarios import public_router, get_db, publish_scenario_version
from app.core.config import settings
from app.db.base import Base
from app.models.intelligence import InspectionScenario, InspectionScenarioVersion, ScenarioNode, ScenarioEdge


async def main():
    settings.ocr_service_base_url = sys.argv[1] if len(sys.argv) > 1 else settings.ocr_service_base_url
    settings.scene_api_key = ""
    settings.scene_result_s3_enabled = False
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with TemporaryDirectory() as directory, Session(engine) as database:
        source = Path(directory) / "ocr-smoke.png"
        image = Image.new("RGB", (640, 150), "white")
        ImageDraw.Draw(image).text((30, 25), "FU5 400A", fill="black", font=ImageFont.load_default(size=65))
        image.save(source)
        scene = InspectionScenario(code="OCR_SMOKE", name="OCR smoke test", mode="WORKFLOW")
        database.add(scene)
        database.flush()
        version = InspectionScenarioVersion(scenario_id=scene.id, version="1", status="DRAFT")
        database.add(version)
        database.flush()
        entries = [
            ("start", "START", {}),
            ("ocr", "OCR", {"image_path": "{{ input.image_path }}"}),
            ("rule", "RULE", {"actual": "{{ nodes.ocr.text }}", "operator": "CONTAINS", "expected": "FU5"}),
            ("end", "END", {"output": {"text": "{{ nodes.ocr.text }}", "result": "{{ nodes.rule.result }}"}}),
        ]
        for order, (key, kind, config) in enumerate(entries):
            database.add(ScenarioNode(scenario_version_id=version.id, node_key=key, name=key, node_type=kind, sort_order=order, enabled=True, config_json=config))
        for source_node, target_node in zip(entries, entries[1:]):
            database.add(ScenarioEdge(scenario_version_id=version.id, source_node_key=source_node[0], target_node_key=target_node[0], mapping_json={}))
        database.commit()
        publish_scenario_version(version.id, database=database)
        app = FastAPI()
        app.include_router(public_router, prefix="/api/v1/scenarios")
        app.dependency_overrides[get_db] = lambda: database
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/api/v1/scenarios/invoke/OCR_SMOKE", json={"image_path": str(source), "inputs": {}, "enqueue_review": False})
        payload = response.json()
        assert response.status_code == 200, payload
        assert payload["code"] == 0 and payload["result"] == "OK", payload
        assert "FU5" in json.dumps(payload), payload
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
