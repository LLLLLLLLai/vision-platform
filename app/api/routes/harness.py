from typing import Any

from fastapi import APIRouter

from app.harness.runtime import get_harness


router = APIRouter()


@router.get("/harness")
def harness_runtime() -> dict[str, Any]:
    """Expose the active capability composition for operations and diagnostics."""
    return get_harness().describe()
