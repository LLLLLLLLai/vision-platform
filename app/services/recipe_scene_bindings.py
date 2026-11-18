from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models.intelligence import InspectionScenarioVersion, RoiScenarioBinding
from app.models.recipe import RegionOfInterest


def recipe_scene_bindings(database: Session, recipe_ids: list[int]) -> dict[int, dict]:
    if not recipe_ids:
        return {}
    bindings = database.scalars(
        select(RoiScenarioBinding)
        .join(RegionOfInterest, RoiScenarioBinding.roi_id == RegionOfInterest.id)
        .options(selectinload(RoiScenarioBinding.scenario_version).selectinload(InspectionScenarioVersion.scenario))
        .where(
            RegionOfInterest.recipe_id.in_(recipe_ids),
            RegionOfInterest.is_deleted.is_(False),
            RoiScenarioBinding.enabled.is_(True),
            RoiScenarioBinding.is_deleted.is_(False),
        )
    ).all()
    return {
        binding.roi_id: {
            "roi_id": binding.roi_id,
            "scenario_id": binding.scenario_version.scenario_id,
            "scenario_version_id": binding.scenario_version_id,
            "scenario_name": binding.scenario_version.scenario.name,
            "scenario_code": binding.scenario_version.scenario.code,
            "version": binding.scenario_version.version,
            "input_mapping_json": binding.input_mapping_json,
        }
        for binding in bindings
    }
