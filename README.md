# Vision Platform

面向汽车电子装配错装、漏装、混装检测的工业视觉智能平台。

## 当前文档

- [OCR 流程节点与 VLM 密钥保存说明](docs/OCR-Node-and-VLM-Keys.md)

- [场景仅采集模式：使用、接口约定和回归验证](docs/Collection-Only-Scenes.md)

### 无模型时先采集 ROI

场景维护中新建场景时选择“仅采集图片”，无需配置 VLM、提示词或训练模型即可发布。工艺配方 ROI 关联该场景，数据集选择按场景采集 ROI 并开启自动采集。生产调用 `/api/detect` 后，定位与裁剪照常执行，但不调用模型，素材进入“待标注”。仅采集项不参与质量统计、不触发 VLM 复核；请求只要包含仅采集项，接口 `result` 返回 `NG`，成功采集使用正常 `code`，读取/裁剪/保存失败使用异常 `code`。纯采集图片返回原图路径，混合检测图片仍返回结果图。场景设计器测试只检查采集配置，不写入生产素材。模型就绪后在同一场景新建草稿、切换正常检测并测试发布；草稿不会影响生产。

- 当前交接手册：`docs/Vision-Platform-Handover-V3.0.md`
- 场景、数据集与模型架构：`docs/Scene-Dataset-Model-Architecture.md`
- MySQL 8 迁移说明：`docs/MySQL-Migration.md`
- MySQL 表结构：`docs/MySQL-Table-Structure.md`
- 解耦部署方案：`docs/DECOUPLED_DEPLOYMENT_PLAN.md`；独立 GPU 推理服务：`deploy/model-server/README.md`
- 历史系统交接：`docs/Vision-Platform-System-Handover.md`（仅供追溯，不作为当前部署说明）

## 当前架构

```text
相机软件 / 文件服务器 / 外部系统
                |
                v
      POST /api/detect（兼容既有接口）
                |
                v
工艺配方匹配（拉线 + 物料 + 工序 + 相机 + 拍照次数）
                |
                v
ROI / 特征点对齐（确定检测区域）
                |
                v
已发布检测场景（VLM 直接检测或流程编排）
      |                         |
      v                         v
OpenAI 兼容 VLM             已发布 YOLO 模型
                |
                v
结果图、执行记录、复核、人工复判与报表
```

平台不控制相机、PLC、运动轴或光源。相机软件负责拍照与上传，平台负责配方匹配、ROI/场景执行、结果归档与接口返回。

## 已实现

- FastAPI、SQLAlchemy 2.0；示例配置使用 SQLite，实际部署通过 `.env` 的 `DATABASE_URL` 选择 MySQL 8 或 SQLite。
- 工艺配方版本管理、ROI/特征点编辑与对齐、发布和回滚。
- 场景管理：VLM 直接检测与可视化流程编排；每个发布场景均可生成独立 API。
- 数据集管理：测试集 OK/NG 真值、YOLO 检测/分割/分类在线标注、批量 AI 预标注、离线 YOLO ZIP 导入、生产素材自动采集开关，以及可配置的训练图片增强。
- 模型中心：OpenAI 兼容 VLM 配置、YOLO 模型定义、训练任务、训练指标，以及模型版本发布、回退和权重导出。
- 生产检测：两相机图片和同图 ROI 受控并发执行，SMB 下载原图、回写结果图。
- 运营能力：检测记录、场景评测、提示词优化、VLM 异步复核和场景维度报表。
- 图像对齐：固定工位下支持特征点补偿小范围平移和轻微旋转。
- 流程编排的遍历节点支持在同一画布中放入循环体节点、连接端口并设置每步参数；流程检测可通过 `/api/detect` 与场景独立接口调用。
- 模型训练任务详情的图表可点击放大；工艺配方测试支持在当前页面更换图片后重新执行。

## 模型版本与 AI 辅助设计

- 训练模型维护只创建 YOLO 模型；VLM 服务接口单独在“VLM 模型配置”维护。训练模型编码自动生成，名称去除首尾空白并检查重名。
- 模型卡片 → 版本管理 → “切换到此版本”：可在曾发布版本间回退或恢复，不删除权重。**已发布场景固定引用的模型版本不会跟随切换**；如需变更生产模型，在场景草稿中选择目标模型版本、测试后重新发布。
- 场景维护 → 新建场景 → 进入设计页 → “AI 辅助设计”：选择已有 VLM 接口，上传图片并填写要求。可以继续补充要求；VLM 场景生成提示词和输入变量，流程场景生成节点、参数映射及连线。
- 设计预览可编辑直接检测提示词；确认后创建独立 `AI-...` 草稿，不覆盖原场景版本，不自动发布。原场景的生产接口保持不变。需要先测试，再手动发布。
- 辅助生成目前支持开始、结束、VLM、训练模型、规则判断、裁剪、画框节点；循环、HTTP、S3 等复杂节点仍在画布手动配置。AI 生成内容不代表经过生产验证。
- 图片会发送到所选 VLM 端点。生成请求图片临时保存，完成后删除；对话仅保留在当前页面会话，最多6轮，每轮图片不超过20MB、4000万像素。模型响应格式错误时，携带原要求和历史要求重新生成一次；仍失败则保留原场景不变。
- 管理接口：`POST /api/v1/scenarios/{id}/design-assistant`（multipart：`image`、`vlm_model_id`、`requirements`、`conversation_json`）；`POST /api/v1/scenarios/{id}/design-assistant/apply`（生成的设计JSON，保存为新草稿）。应用前重新校验模型、变量、连线，不改变 `/api/detect` 契约。

## MySQL 表结构维护

`docs/mysql/vision_platform_mysql8.sql` 是 SQLAlchemy ORM 生成的 **31 张表的空库建表基线**，不是升级现有 MySQL 库的 `ALTER` 脚本；逐字段说明见 `docs/MySQL-Table-Structure.md`。模型定义或字段变更后运行 `python scripts/generate_mysql_schema.py`，再运行 `python -m unittest discover -s tests -p test_mysql_schema_generator.py` 校验两个文档与 ORM 一致。对现有生产库升级前必须备份并执行受控迁移；`init_database()` 不会自动给已有 MySQL 表补字段。

## 静态资源与离线部署

页面的 CSS、JavaScript 均从本项目的 `/static` 路径加载，不依赖在线 CDN。当前唯一的第三方前端资源是 **Bootstrap 5.3.7**，其编译文件和许可证已随代码保存在：

```text
app/static/vendor/bootstrap/5.3.7/css/bootstrap.min.css
app/static/vendor/bootstrap/5.3.7/js/bootstrap.bundle.min.js
app/static/vendor/bootstrap/5.3.7/LICENSE
```

这些文件已与 [Bootstrap 5.3.7 npm 发布包](https://www.npmjs.com/package/bootstrap/v/5.3.7)逐项核对 SHA-256；`bootstrap.bundle.min.js` 已包含页面所需的 Popper。项目自有样式和脚本位于 `app/static/css/`、`app/static/js/`。迁移到无公网环境时，只需将仓库中的 `app/static/` 一并部署，不需要在浏览器端额外下载 CSS 或 JS；Python 依赖、模型权重、数据库、SMB/S3 和 VLM 服务仍须按部署环境准备。

仓库包含 `vision-models/paddleocr/` 下的 PP-OCRv5 mobile 检测与识别推理模型（普通 Git 文件，无需 Git LFS），可随代码离线部署。运行时 `.env`、加密主密钥、生产图片、训练产物及其他模型权重仍由 `.gitignore` 排除，部署前应通过安全渠道单独迁移，不要提交凭证或生产数据。

## 模型接入与 OCR 服务

以下旧版独立服务及其启动、下载脚本已删除，不再用于生产检测：

```text
DINOv2
SAM2
PaddleOCR
本地 Qwen3-VL
```

当前平台通过“VLM 模型配置”调用用户维护的 **OpenAI 兼容 VLM 接口**，并通过流程场景调用已发布的 YOLO 权重。新增加的 **OCR 文字识别节点** 使用独立环境中的 `ocr_service/server.py`，与旧版算法接口无关。默认监听本机 `9024`，通过 `start-ocr.ps1` 或 `bash start-ocr.sh` 启动；输入图片，输出文字、置信度和文字坐标，不自动判断 OK/NG。模型目录配置为空时自动读取仓库附带权重，详见 [OCR 部署说明](docs/OCR-Node-and-VLM-Keys.md)。

- `GET /api/v1/algorithms/status` 仅返回旧服务已下线说明。
- `POST /api/v1/algorithms/vlm/judge` 返回 `410 Gone`，请改用已发布 VLM 场景接口。
- 生产配方中的每个启用 ROI 必须关联已发布检测场景；未关联时 `/api/detect` 返回明确的 `ERROR`，不会回退调用旧模型或误判为 `OK`。
- `vision-models/` 中的权重不会因本次变更删除，可继续作为 YOLO 基础权重或已发布权重使用。

## 训练模型版本与说明

在“训练模型维护”卡片点击“模型说明”，可查看模型用途、配置类别、已导入权重的实际类别、标注方式和对应任务类型的输出结构示例。检测/分割输出目标列表，分类输出 Top1/Top5；示例数值不是实际推理结果，推理类别名称以权重文件为准。

点击“查看版本”，可对每个有权重的版本下载 `.pt` 文件；下载只允许读取已登记在 `vision-models/` 或配置的训练产物目录下的权重。已归档版本可“回退启用”，重新成为当前发布版本；草稿版本须先发布。回退前会检查权重文件是否仍存在，不会删除或覆盖其他版本文件。

流程节点固定引用模型**版本 ID**。回退模型卡片的当前版本不会自动修改已发布场景的节点绑定；原节点仍可运行其已归档但曾发布的版本。若要让生产流程改用回退后的权重，需在场景草稿中选择该版本并重新发布场景。

## 目录

```text
app/
  api/                    REST API 路由
  core/                   配置
  db/                     数据库会话、初始化和轻量升级
  models/                 配方、检测、场景、数据集和世界模型 ORM
  services/               检测引擎、场景运行时、SMB、训练、异步 Worker
  harness/                OpenCV 本地规则与图像对齐插件
  static/                 管理页面静态资源
  templates/              管理页面模板
config/                   环境和 Harness 配置说明
scripts/                  初始化、迁移和测试脚本
tests/                    自动化测试
ocr_service/              独立 PaddleOCR 服务和依赖清单
vision-models/paddleocr/   附带的 OCR 推理权重、许可证及校验和
vision-models/             其他本地模型权重，不纳入 Git
uploads/                  配方图和数据集素材，不纳入 Git
detection_results/        原图暂存、ROI 图和结果图，不纳入 Git
data/training_runs/       YOLO 训练产物，不纳入 Git
```

## 启动

Windows：

```powershell
cd C:\Users\Administrator\Desktop\vision-platform
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
.\start.ps1
```

Linux：

```bash
# 方案一：项目 .venv（优先使用）
python3.11 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
cp .env.example .env
chmod +x start.sh stop.sh
./start.sh
# 需要停止服务时再执行
./stop.sh
```

如果项目没有 `.venv/bin/python`，可复用 Conda 环境：

```bash
conda activate vision-platform
python -m pip install -r requirements.txt
test -f .env || cp .env.example .env
chmod +x start.sh stop.sh
./start.sh
# 需要停止服务时再执行 ./stop.sh
```

非交互式终端可先设置 `VISION_CONDA_PREFIX=/opt/miniconda3/envs/vision-platform`。脚本启动前初始化数据库，后台运行服务，PID 和日志分别保存在 `logs/platform.pid`、`logs/platform.log`；重复启动不会创建第二个进程，`stop.sh` 只停止该 PID 对应的本项目进程。若 `.venv` 存在，它始终优先于 Conda。拆分为控制端和检测端的生产部署仍建议使用 `deploy/application-server/` 中的 systemd 单元；这两个脚本只管理当前项目的一份服务进程，不启动外部 VLM 或 YOLO 服务。

访问地址：

- 管理页面：`http://127.0.0.1:9010`
- API 文档：`http://127.0.0.1:9010/docs`
- 健康检查：`http://127.0.0.1:9010/api/v1/health`

生产环境需在 `.env` 中配置数据库、SMB、`SCENE_API_KEY` 和 VLM 密钥；不要提交 `.env`。

## 检测接口

兼容生产接口：`POST /api/detect`

```json
{
  "line": "L4",
  "times": 1,
  "operation": "AS15",
  "materialCode": "CN000798",
  "image_paths": [
    "\\\\server\\share\\AS15-CAMERA1PICTURE1-CN000798263700002-20260915134635.jpg",
    ""
  ]
}
```

- 空图片路径会跳过。
- 平台从文件名解析 `CAMERA1PICTURE1` 和产品条码；传入的 `line`、`materialCode`、`operation`、`times` 与图片相机信息共同匹配已发布工艺配方。
- 顶层返回继续兼容 `code`、`message`、`result` 与 `image_paths`；详情保存在检测记录中。

### 流程场景的系统图片变量

工艺配方调用已发布流程场景时，开始节点自动提供：

| 变量 | 含义 |
|---|---|
| `input.image_path` | 本地 ROI 裁剪图，供模型检测。 |
| `input.original_image_path` | 本地暂存的原始整图，供后续整图处理。 |
| `input.source_image_path` | 调用方传入的原图路径（如 SMB UNC），供定位原始来源。 |
| `input.roi_bbox` | ROI 在原图上的像素框 `[x1, y1, x2, y2]`，供坐标转换。 |

单独调用场景接口时只有一张输入图，`image_path`、`original_image_path` 与 `source_image_path` 默认相同，`roi_bbox` 不提供。ROI 模型输出的 bbox 是 **ROI 局部坐标**；若要画在原始整图上，必须先按 `roi_bbox` 的左上角平移，不能直接使用局部坐标。`/api/detect` 的结果图仍由平台通过 SMB 回写，不会因场景接口启用 S3 而改变。

### 已发布场景接口的 S3 结果图

外部调用 `POST /api/v1/scenarios/invoke/{scene_code}` 时，先设置 `SCENE_RESULT_S3_ENABLED=true`、`SCENE_RESULT_S3_BUCKET` 以及可选的 S3 端点、凭证，再在需要上传的**流程场景**中添加“**S3 上传**”节点。推荐流程为“模型检测 → 图片画框 → S3 上传 → 结束”；在上传节点的“待上传图片”选择 `{{ nodes.annotate_1.image_path }}`，结束节点可返回 `{{ nodes.s3_1.result_image_path }}`。不添加上传节点的场景不会因为 S3 配置已启用而上传。VLM 直接场景没有画布，若必须沿用旧的自动上传方式，可单独设置 `SCENE_RESULT_S3_AUTO_PUBLISH=true`；此兼容模式默认关闭。流程中已执行 S3 节点时不会再自动重复上传。

请求可选传 `result_s3_key`，例如 `line-L4/2026-11-01/part-001_result.jpg`，覆盖上传节点设置的对象键；不传且节点键为空时按场景、日期、执行 ID 自动生成唯一键。键只能位于配置桶及 `SCENE_RESULT_S3_PREFIX` 下，必须以 `.jpg` 或 `.jpeg` 结尾。PNG 输入会转成 JPEG 上传。指定的键若重复，S3 对象可能被覆盖，调用方应保证唯一。成功执行上传节点后，响应顶层 `result_image_path`、`image_paths` 为 `s3://` URI；节点输出也可供后续节点使用。`image_path` 仍须是平台可读取的输入路径，**S3 节点只负责上传，不负责下载输入**。节点上传失败则本次场景执行失败，执行追踪保留错误原因；兼容自动上传模式失败返回 `code=5002`，检测结论不变。`/api/detect` 原有 SMB 回写逻辑不变。生产环境应配置 `SCENE_API_KEY`，并确保桶权限只覆盖目标前缀；`s3://` URI 不是可直接公开访问的 HTTPS 链接。

### 多目标裁剪与逐项复核

目标检测或分割模型的 `objects` 可能包含多个目标。现在将循环能力拆成两个独立节点，不需要把主画布的连线绕回节点：

- 图片裁剪节点的“目标列表”选 `{{ nodes.model_1.objects }}`，输出 `{{ nodes.crop_1.crops }}`；每项保留类别、置信度、原图 bbox 与裁剪图路径。只填 bbox 时保持原有单框裁剪行为。分割目标当前按其 bbox 裁剪，而不是按 mask 抠图；分类模型没有 bbox，不能用于批量裁剪。
- **遍历处理**：连接“训练模型 → 图片裁剪（目标列表）→ 遍历处理 → 后续节点”，输入数组填 `{{ nodes.crop_1.crops }}`。每张裁剪图进入一次内部子流程；内部可配置 VLM、训练模型、裁剪、规则、分支、Web 接口、S3 上传等节点。当前项使用 `{{ input.item.* }}`，内部上游使用 `{{ nodes.内部节点键.字段 }}`，外层上游使用 `{{ parent.nodes.节点键.字段 }}`。结果保存在 `items[]`，可选择全部 OK、任一 OK 或只收集结果。零项为 `UNCERTAIN`，超过最多 100 项或子节点报错时不会静默判 OK。
- **条件循环**：配置初始循环变量、每轮变量更新和退出条件；内部子流程每轮执行一次，可使用 `{{ state.变量名 }}`。更新值可引用内部节点输出，或选择“累加”“追加到数组”。每轮结束后检查退出条件；满足时返回本轮输出，未在最多 20 轮或 300 秒内满足时返回 `ERROR`。逐轮输入、输出、耗时和退出判定保存在 `rounds[]`。
- 两种节点都可以点击主画布卡片上的“展开循环体”，让循环节点在**同一画布**中展开为大容器，直接查看和编辑内部处理节点；展开时其他主流程节点仅临时避让，原坐标不变，收起即恢复。可从左侧节点库拖入容器内部（拖到未展开的循环节点也会自动展开），或点击“添加内部节点”；还可调整顺序和选择后续连接。条件分支可分别连接到后续节点或本轮结束。内部不允许再嵌套循环，最多 20 个处理节点，编辑后点击“保存内部节点”。新建节点请选这两种；原有 `LOOP` 节点保留旧版“逐项 VLM”“自定义循环体”“仅整理集合”配置，不自动迁移或改变已发布流程的行为。
- 图片画框节点仍可引用原模型的 `objects`，只执行一次；S3 上传节点可上传最终标注图。结束节点可同时返回遍历结果 `{{ nodes.iteration_1.items }}`、条件循环状态 `{{ nodes.condition_loop_1.state }}` 和上传后的图片路径。

## 新增或修改检测能力

1. 在“VLM 模型配置”维护 OpenAI 兼容地址、模型名和密钥，测试连接。
2. 创建 VLM 场景，或创建流程场景并组合已发布 YOLO、VLM、裁剪、规则和 Web API 节点。
3. 使用测试数据集评测场景；确认后发布场景版本。
4. 在工艺配方 ROI 中绑定该已发布场景并填写场景字段值。
5. 保存草稿配方并进行测试，确认后发布配方。

只有已发布的场景与配方会进入生产检测链路。

## 训练图片增强

图片训练数据集可点击“一键 AI 标注全部原图”，选择同类型的已发布 YOLO 模型、置信度和已有标注处理方式。单张与批量 AI 自动标注成功后均直接保存为 `LABELED`（已标注），无需复核或再次保存，可直接用于训练；仍可手动编辑。默认“保留”保留旧标注，并按同类别、区域重合度去重合并新目标，分类则保持已有类别；“替换”使用本次模型结果，旧内容保存在新标注的 `previous_annotation` 元数据中。修改标注时相关增强图会失效。调用失败、没有可用标注或分类类别不在字典中，不覆盖原标注，也不自动把空结果当成负样本。检测/分割的新类别追加到字典，已有类别顺序不变。批量任务在后台运行并显示已标注、保留/跳过和失败数量。

“AI 已标注”筛选是“已标注”的来源子集。标注元数据保留模型、置信度及 AI 来源，不伪装成人工真值。启动时自动将历史 `AI_SUGGESTED` 中有效的训练标注转为已标注；无效内容保留但回到待标注并记录原因，迁移可重复执行，不增加数据库字段。自动采用模型结果可能延续模型误标，训练前建议抽样检查；评测集 OK/NG 人工真值流程不变。

生产素材自动采集可在数据集详情中切换启用/关闭：可按已发布场景收集 ROI、按已发布检测配方收集所有启用 ROI（不要求 ROI 绑定场景），或按配方收集相机原图。只采集、未运行 AI 标注的图片仍为待标注，按内容去重并受数据集数量上限约束，不会直接作为训练真值或生产判定依据。

在“数据集维护”选择图片训练数据集，完成原图标注后，点击“数据增强”。可选全部已标注原图，或在素材卡片中先选部分原图；自行配置每张生成数量、随机种子、翻转、旋转、亮度、对比度、饱和度、模糊和噪声范围。页面会预估生成数量并自动分批（每批最多计划生成 200 张），显示处理进度，可停止后续批次；整次操作不再受 200 张的总量限制。关闭页面会中断后续批次，已完成批次会保留；单次 API 请求仍有 500 张上限，防止请求耗尽服务资源。生成图片会在素材列表中标记为“增强”，并记录来源与参数。

增强参数在提交前检查空值、上下限和范围顺序；接口校验错误显示具体字段和原因，不再显示 `[object Object]`。批次失败时保留弹窗、参数与完成进度，已成功的批次不丢失；列表刷新失败会单独提示，避免误以为生成失败而重复操作。旋转范围为 -45～45 度，每张原图可生成 1～20 张。

目标检测标注框按类别名称分配稳定的彩色配色，同类别始终同色，使用细边框和透明填充，不遮挡图片；类别名称在“已标注对象”列表显示。填充不依赖浏览器的 `color-mix()` 支持，避免 SVG 默认黑色填充。

平台先按原图分组固定训练/验证/测试划分，仅对训练集生成增强图，并同步变换检测框与分割轮廓；修改或删除原图标注后，其旧增强图自动失效。增强只增加训练样本的外观变化，不增加独立的真实样本，不能替代生产数据验证。对于颜色、文字、方向等敏感场景，避免选择会改变检测真值的变换，并在训练前抽查增强图与标注。

“导入离线标注包”仅用于图片训练集，接收 `.zip`（单包最多 2000 张图片、解压后不超过 1 GB），不是单张图片或模型权重。目标检测使用 `images/train/a.jpg` 对应 `labels/train/a.txt`，每行是 `类别编号 中心X 中心Y 宽 高`；目标分割使用相同路径，但每行是 `类别编号 X1 Y1 X2 Y2 X3 Y3 ...` 的轮廓；图片分类可按 `images/train/类别名/a.jpg` 分目录，或给图片配同名 `.txt`（仅一个类别）。检测/分割坐标均为 0～1 比例值。类别编号从 0 开始，压缩包可包含 `classes.txt` 或 `data.yaml`，若数据集已有类别，其名称和顺序必须一致。页面“格式说明”可查看示例；普通评测集不支持此导入方式。
