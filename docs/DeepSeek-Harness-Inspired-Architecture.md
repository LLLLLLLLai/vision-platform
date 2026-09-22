# Harness 风格技术栈说明

## 目标

平台继续采用 Python、FastAPI 和独立模型 HTTP 服务，不把生产检测链路替换为 DeepSeek Harness。本次更新借鉴 [DeepSeek Harness](https://github.com/deepseek-ai/deepseek-harness) 的“核心极小、能力皆插件、运行档案组合”的思想，使模型替换、服务启停和生产/配置场景切换不影响既有 `POST /api/detect` 合同。

DeepSeek Harness 目前属于开发者预览，适合参考架构模式，不应直接作为产线关键实时链路的运行时依赖。

## 当前技术栈

| 层级 | 当前实现 | 作用 |
| --- | --- | --- |
| 平台内核 | Python 3.11 + FastAPI | Web、API、生命周期管理与检测编排入口 |
| 插件运行时 | `app/harness` | 注册插件、选择能力路由、按运行档案装配模型能力 |
| 规则与流程 | `InspectionEngine` + 配方/ROI/规则 | 将工业配方转为确定性的检测执行计划 |
| 模型适配 | `AlgorithmServiceClient` | 通过插件代码调用 DINOv2、OCR、Grounding DINO、SAM2、Qwen3-VL |
| 数据 | SQLAlchemy + SQLite | 当前保存配方、ROI、检测记录、审核状态；生产建议迁移 PostgreSQL |
| 向量 | NumPy FP16 矩阵文件 | 保存按配方层级组织的 ROI 参考 Embedding |
| 前端 | Jinja2 + 本地 Bootstrap/JavaScript/CSS | 离线管理页面与模型服务监控 |
| 服务运维 | 模型服务管理页 + PID/健康检查/调用日志 | 模型启动、停止、日志和健康状态 |

## 与 DeepSeek Harness 的对应关系

| DeepSeek Harness 思想 | Vision Platform 实现 |
| --- | --- |
| Cordis 核心与插件生命周期 | `PluginRuntime`，只管理平台注册、能力路由和运行档案 |
| Plugin manifest | `PluginManifest`，描述模型名称、能力、地址配置、启动脚本和 Python 环境 |
| Profile / Bundle | `config/harness.json`，以 `production`、`minimal`、`configuration` 组合能力 |
| Tools / Models | 独立 HTTP 模型服务，例如 DINOv2、PaddleOCR、Qwen3-VL |
| Agent loop | `InspectionEngine`：配方匹配 → ROI 裁剪/对齐 → 模型调用 → 规则决策 |
| 会话与观测 | 检测记录、模型调用日志、模型服务日志和报表 |

## 运行档案

运行档案在 `config/harness.json` 定义，通过 `.env` 的 `HARNESS_PROFILE` 选择：

- `production`：全量生产能力，固定 ROI 与按需 VLM 复核。
- `minimal`：仅 DINOv2、PaddleOCR、OpenCV，用于模型故障排查。
- `configuration`：启用候选框、分割和 VLM，供配方配置与调试使用。

`capability_routes` 显式规定某一能力由哪个插件提供。例如 `text.ocr -> paddleocr`、`reference.similarity -> dinov2`。将来接入 PP-OCR-VL、SigLIP、YOLO-World 或其他模型时，只需注册新插件并在目标档案切换路由；配方字段和检测接口无需改动。

## 已注册插件

| 插件代码 | 主要能力 | 当前职责 |
| --- | --- | --- |
| `dinov2` | `image.embedding`、`reference.similarity`、`object.presence` | ROI 特征与相似度判断 |
| `paddleocr` | `text.ocr` | 专用文字识别 |
| `opencv_rules` | `image.color`、`image.alignment`、`image.quality` | 颜色、基准点对齐、质量门禁 |
| `grounding_dino` | `object.localization`、`recipe.auto_discovery` | 配方配置时生成候选框 |
| `sam2` | `object.segmentation`、`harness.segmentation` | 不规则物体和线束的辅助分割 |
| `qwen3_vl` | `vlm.judgement`、`vlm.compare`、`object.inventory` | 低置信度、双图与离线复核 |

## 运维接口

- `GET /api/v1/health`：返回应用状态、当前档案和已启用插件。
- `GET /api/v1/harness`：返回能力到插件的实际路由。
- `GET /api/v1/model-services`：返回模型服务健康状态、端口、启动脚本和日志位置。

切换 `HARNESS_PROFILE` 或 `HARNESS_DISABLED_PLUGINS` 后需要重启平台服务，使运行档案重新装配。

## 新模型接入流程

1. 新建一个模型 HTTP 服务，并定义稳定的健康检查和请求/响应契约。
2. 在 `app/harness/builtin.py` 新增 `PluginManifest`，声明其能力、地址配置、启动脚本和环境。
3. 在 `config/harness.json` 的目标运行档案中启用插件，并用 `capability_routes` 选择它。
4. 在 `AlgorithmServiceClient` 或新增适配器中实现该能力的统一调用。
5. 添加单元测试和模型服务健康检查，再切换生产档案。

这样能限制模型升级的影响范围：新模型先在 `configuration` 或 `minimal` 档案验证，确认后才切到 `production`。
