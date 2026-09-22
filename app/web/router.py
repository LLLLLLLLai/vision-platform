from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from app.core.config import PROJECT_ROOT, settings


router = APIRouter()
templates = Jinja2Templates(directory=Path(PROJECT_ROOT / "app/templates"))

WORKSPACE_VIEWS = {
    "editorView": "workspace_editor",
    "libraryView": "workspace_library",
    "recordsView": "workspace_records",
    "reportsView": "workspace_reports",
}
WORKSPACE_PAGES = {
    "editor": {
        "view": "editorView",
        "active_section": "workspace_editor",
        "title": "配置编辑",
        "description": "按拉线、物料、工序、相机和拍照次数维护工艺配方、ROI 与检测规则。",
    },
    "library": {
        "view": "libraryView",
        "active_section": "workspace_library",
        "title": "工艺配方库",
        "description": "检索、测试和维护已创建的工艺配方。",
    },
    "records": {
        "view": "recordsView",
        "active_section": "workspace_records",
        "title": "检测记录",
        "description": "查看外围接口调用、场景执行和检测结果明细。",
    },
    "reports": {
        "view": "reportsView",
        "active_section": "workspace_reports",
        "title": "统计报表",
        "description": "按时间范围查看生产检测趋势、OK/NG 率和场景指标。",
    },
}
SCENE_PAGES = {
    "maintenance": {
        "active_section": "scene_maintenance",
        "title": "场景维护",
        "description": "创建、编辑和发布可被配方 ROI 调用的检测场景。",
    },
    "optimization": {
        "active_section": "scene_optimization",
        "title": "场景优化",
        "description": "使用已标注测试集循环优化 VLM 提示词，结果只生成候选草稿，不会自动上线。",
    },
    "evaluation": {
        "active_section": "scene_evaluation",
        "title": "场景评测",
        "description": "使用已发布场景与测试数据集，统计准确率、漏判和误判。",
    },
}
MODEL_PAGES = {
    "vlm": {
        "active_section": "model_vlm",
        "title": "VLM 模型配置",
        "description": "维护 OpenAI 兼容接口的 VLM 服务，用提示词驱动检测、优化和独立复核。",
    },
    "registry": {
        "active_section": "model_registry",
        "title": "训练模型维护",
        "description": "定义可训练模型的名称、架构、任务类型和识别类别；支持 YOLO 与 ResNet。",
    },
    "training": {
        "active_section": "model_training",
        "title": "模型训练",
        "description": "选择训练模型和数据集后，系统按 70% / 20% / 10% 自动切分并提交 GPU 调度队列。",
    },
}


@router.get("/")
def dashboard() -> RedirectResponse:
    """The recipe library is now the product entry page, not a dashboard."""

    return RedirectResponse(url="/recipes/library", status_code=307)


def _workspace_page(request: Request, selected_view: str, *, page: dict[str, str] | None = None):
    return templates.TemplateResponse(
        request=request,
        name="workspace.html",
        context={
            "app_name": settings.app_name,
            "active_section": WORKSPACE_VIEWS[selected_view],
            "active_workspace_view": selected_view,
            "workspace_page_title": page["title"] if page else "产品世界模型与检测配方",
            "workspace_page_description": page["description"] if page else "把产品对象、相机视图、ROI、检测能力和质量规则组织成可复用的数字产品地图。",
        },
    )


@router.get("/workspace")
def workspace(request: Request, view: str | None = None):
    """Legacy workspace URL retained for existing bookmarks and integrations."""

    selected_view = view if view in WORKSPACE_VIEWS else "libraryView"
    return _workspace_page(request, selected_view)


@router.get("/recipes/{page}")
def recipe_page(request: Request, page: str):
    if page not in {"editor", "library"}:
        raise HTTPException(status_code=404, detail="工艺配方页面不存在。")
    config = WORKSPACE_PAGES[page]
    return _workspace_page(request, config["view"], page=config)


@router.get("/operations/{page}")
def operations_page(request: Request, page: str):
    if page not in {"records", "reports"}:
        raise HTTPException(status_code=404, detail="质量运营页面不存在。")
    config = WORKSPACE_PAGES[page]
    return _workspace_page(request, config["view"], page=config)


@router.get("/model-services")
def legacy_model_services_page() -> RedirectResponse:
    return RedirectResponse(url="/models/vlm", status_code=307)


def _scene_page(request: Request, page: str):
    config = SCENE_PAGES.get(page)
    if config is None:
        raise HTTPException(status_code=404, detail="场景页面不存在。")
    return templates.TemplateResponse(
        request=request,
        name="scenes.html",
        context={
            "app_name": settings.app_name,
            "active_section": config["active_section"],
            "active_scene_view": page,
            "page_title": config["title"],
            "page_description": config["description"],
        },
    )


@router.get("/scenes")
def scenes(request: Request):
    return _scene_page(request, "maintenance")


@router.get("/scenes/designer/{scenario_id}")
def scene_designer(request: Request, scenario_id: int):
    return templates.TemplateResponse(
        request=request,
        name="scene_designer.html",
        context={
            "app_name": settings.app_name,
            "active_section": "scene_maintenance",
            "scenario_id": scenario_id,
        },
    )


@router.get("/scenes/{page}")
def scene_page(request: Request, page: str):
    return _scene_page(request, page)


@router.get("/datasets")
@router.get("/datasets/maintenance")
def datasets(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="datasets.html",
        context={
            "app_name": settings.app_name,
            "active_section": "dataset_maintenance",
        },
    )


def _model_page(request: Request, page: str):
    config = MODEL_PAGES.get(page)
    if config is None:
        raise HTTPException(status_code=404, detail="模型页面不存在。")
    return templates.TemplateResponse(
        request=request,
        name="models.html",
        context={
            "app_name": settings.app_name,
            "active_section": config["active_section"],
            "active_model_view": page,
            "page_title": config["title"],
            "page_description": config["description"],
        },
    )


@router.get("/models")
def models(request: Request):
    return _model_page(request, "vlm")


@router.get("/models/{page}")
def model_page(request: Request, page: str):
    return _model_page(request, page)
