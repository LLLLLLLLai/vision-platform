from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.templating import Jinja2Templates

from app.core.config import PROJECT_ROOT, settings


router = APIRouter()
templates = Jinja2Templates(directory=Path(PROJECT_ROOT / "app/templates"))


@router.get("/")
def dashboard(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="dashboard.html",
        context={
            "app_name": settings.app_name,
            "navigation": [
                "产品管理",
                "工位管理",
                "配方管理",
                "ROI 编辑",
                "视觉标准库",
                "检测调试",
                "检测历史",
                "算法配置",
            ],
        },
    )

