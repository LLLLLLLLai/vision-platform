from fastapi import APIRouter, HTTPException, Query

from app.services.model_service_manager import (
    get_service_definition,
    list_service_statuses,
    service_logs,
    start_service,
    stop_service,
)


router = APIRouter()


@router.get("")
async def list_model_services() -> dict:
    services = await list_service_statuses()
    return {
        "services": services,
        "summary": {
            "total": len(services),
            "ready": sum(service["status"] == "READY" for service in services),
            "starting": sum(service["status"] == "STARTING" for service in services),
            "problem": sum(service["status"] == "ERROR" for service in services),
            "stopped": sum(service["status"] == "STOPPED" for service in services),
        },
    }


@router.post("/{code}/start")
def start_model_service(code: str) -> dict:
    try:
        return start_service(get_service_definition(code))
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/{code}/stop")
def stop_model_service(code: str) -> dict:
    try:
        return stop_service(get_service_definition(code))
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/{code}/logs")
def read_model_service_logs(
    code: str,
    lines: int = Query(default=200, ge=20, le=1000),
) -> dict:
    try:
        return service_logs(get_service_definition(code), lines)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
