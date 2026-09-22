from __future__ import annotations

from app.harness.contracts import PluginManifest


BUILTIN_PLUGINS: tuple[PluginManifest, ...] = (
    PluginManifest(
        code="grounding_dino",
        name="Grounding DINO",
        category="开放词汇定位",
        capabilities=("object.localization",),
        url_setting="grounding_service_url",
        launch_script="scripts/run_grounding.py",
        python_environments=(".venv-qwen", ".venv"),
        description="预留开放词汇定位能力，供后续场景节点扩展。",
    ),
    PluginManifest(
        code="dinov2",
        name="DINOv2",
        category="视觉特征与相似度",
        capabilities=("image.embedding", "reference.similarity", "object.presence"),
        url_setting="dinov2_service_url",
        launch_script="scripts/run_dinov2.py",
        python_environments=(".venv",),
        description="提取 ROI 特征并执行参考向量相似度计算。",
    ),
    PluginManifest(
        code="paddleocr",
        name="PaddleOCR",
        category="专用 OCR",
        capabilities=("text.ocr",),
        url_setting="paddleocr_service_url",
        launch_script="scripts/run_ocr.py",
        python_environments=("ocr_service/.venv", ".venv"),
        description="识别标签、线束与零件表面的字符。",
    ),
    PluginManifest(
        code="opencv_rules",
        name="OpenCV Rules",
        category="本地规则",
        capabilities=("image.color", "image.alignment", "image.quality"),
        description="执行颜色、图像质量、几何和基准点对齐等确定性规则。",
    ),
    PluginManifest(
        code="qwen3_vl",
        name="Qwen3-VL",
        category="VLM 复核",
        capabilities=("vlm.judgement", "vlm.compare", "object.inventory"),
        url_setting="qwen_vl_service_url",
        launch_script="scripts/run_qwen_vl.py",
        python_environments=(".venv-qwen", ".venv"),
        description="用于低置信度复核、双图审核和物体清单辅助。",
    ),
    PluginManifest(
        code="sam2",
        name="SAM2",
        category="区域分割",
        capabilities=("object.segmentation", "harness.segmentation"),
        url_setting="sam2_service_url",
        launch_script="scripts/run_sam2.py",
        python_environments=(".venv", ".venv-qwen"),
        description="为线束和不规则物体提供配置辅助分割。",
    ),
)
