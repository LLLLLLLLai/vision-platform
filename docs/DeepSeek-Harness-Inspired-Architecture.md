# Harness 风格技术栈说明

## 目标

平台继续采用 Python 和 FastAPI，不把生产检测链路替换为 DeepSeek Harness。本次更新借鉴 [DeepSeek Harness](https://github.com/deepseek-ai/deepseek-harness) 的“核心极小、能力皆插件、运行档案组合”思想，使本地规则能力可控地接入生产检测，而 VLM、YOLO 调用由场景运行时负责。

DeepSeek Harness 目前属于开发者预览，适合参考架构模式，不应直接作为产线关键实时链路的运行时依赖。

## 当前技术栈

| 层级 | 当前实现 | 作用 |
| --- | --- | --- |
| 平台内核 | Python 3.11 + FastAPI | Web、API、生命周期管理与检测编排入口 |
| 插件运行时 | `app/harness` | 注册插件、选择能力路由、按运行档案装配模型能力 |
| 规则与流程 | `InspectionEngine` + 配方/ROI/场景 | 将工业配方转为确定性的场景执行计划 |
| 模型适配 | `scenario_runtime` | 调用用户配置的 OpenAI 兼容 VLM 和已发布 YOLO 模型 |
| 数据 | SQLAlchemy + MySQL 8 | 生产保存配方、ROI、检测记录、审核状态；本地迁移前兼容 SQLite |
| 向量 | 历史兼容数据 | 原 DINOv2 参考向量仅保留追溯，不再新增 |
| 前端 | Jinja2 + 本地 Bootstrap/JavaScript/CSS | 离线管理页面与模型服务监控 |
| 服务运维 | VLM 连接测试、YOLO 训练与发布 | 管理外部 VLM 配置和已发布训练权重 |

## 与 DeepSeek Harness 的对应关系

| DeepSeek Harness 思想 | Vision Platform 实现 |
| --- | --- |
| Cordis 核心与插件生命周期 | `PluginRuntime`，只管理平台注册、能力路由和运行档案 |
| Plugin manifest | `PluginManifest`，描述本地规则插件的名称与能力 |
| Profile / Bundle | `config/harness.json`，以 `production`、`minimal`、`configuration` 组合本地规则能力 |
| Tools / Models | 场景运行时调用用户配置的 VLM 和已发布 YOLO 模型 |
| Agent loop | `InspectionEngine`：配方匹配 → ROI 裁剪/对齐 → 场景执行 → 结果决策 |
| 会话与观测 | 检测记录、场景执行记录、异步任务和报表 |

## 运行档案

运行档案在 `config/harness.json` 定义，通过 `.env` 的 `HARNESS_PROFILE` 选择：

- `production`：OpenCV 颜色、图像质量和特征点对齐，模型判断由发布场景执行。
- `minimal`：同一套本地规则能力，可用于平台故障排查。
- `configuration`：同一套本地规则能力，供配方配置与调试使用。

`capability_routes` 显式规定某一**本地规则**由哪个插件提供，例如 `image.color -> opencv_rules`。未来接入新模型时，优先作为 VLM 配置或已发布训练模型接入场景运行时；只有确定性本地规则才需要注册 Harness 插件。

## 已注册插件

| 插件代码 | 主要能力 | 当前职责 |
| --- | --- | --- |
| `opencv_rules` | `image.color`、`image.alignment`、`image.quality` | 颜色、基准点对齐、质量门禁 |

## 运维接口

- `GET /api/v1/health`：返回应用状态、当前档案和已启用插件。
- `GET /api/v1/harness`：返回能力到插件的实际路由。
- VLM 配置页提供 OpenAI 兼容连接测试；YOLO 训练和模型版本页提供训练状态与产物。

切换 `HARNESS_PROFILE` 或 `HARNESS_DISABLED_PLUGINS` 后需要重启平台服务，使运行档案重新装配。

## 新模型接入流程

1. VLM：在模型配置页新建 OpenAI 兼容配置并测试连接；或创建/训练 YOLO 模型版本。
2. 在场景中选择发布的 VLM/YOLO，并完成场景测试、评测和发布。
3. 仅当需要新的确定性本地规则时，在 `app/harness/builtin.py` 新增 `PluginManifest`。
4. 在 `config/harness.json` 的目标运行档案中启用插件，并用 `capability_routes` 选择它。
5. 添加单元测试后再切换生产档案。

这样能限制模型升级的影响范围：新模型先在 `configuration` 或 `minimal` 档案验证，确认后才切到 `production`。
