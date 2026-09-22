# Vision Platform V2.0 交接文档

> 更新日期：2026-09-22
> 代码分支：`develop`
> 适用范围：汽车电子装配的错装、漏装、混装检测；支持工艺配方检测、场景编排、VLM 检测、YOLO 训练与数据集管理。

## 1. 系统定位与边界

本系统是一个工业视觉智能平台，核心目标是把产线的“当前工艺配方应检查什么”与 AI 的“如何检查”分离：

```text
外部相机软件 / MES
        │ POST /api/detect
        ▼
工艺配方匹配
        │ 拉线 + 物料 + 工序 + 相机 + 拍照次数
        ▼
ROI 裁剪 + 特征点对齐
        ▼
已发布检测场景
        │ VLM 直检 / 工作流 / 已发布 YOLO 模型
        ▼
规则汇总、结果图、检测记录、异步复核
        ▼
OK / NG / ERROR 与结果图片路径
```

当前不直接控制 PLC、相机、运动轴或光源；相机软件负责拍照、上传图片并调用检测接口。模型权重、训练数据、上传图片、检测结果与密钥均不提交 Git。

## 2. 运行架构

### 2.1 服务组成

| 组件 | 代码位置 | 主要职责 |
| --- | --- | --- |
| 平台 API 与管理页面 | `app/main.py`、`app/api/`、`app/web/` | 配方、场景、数据集、模型、检测记录与外部接口。 |
| 检测执行引擎 | `app/services/inspection_engine.py` | 裁剪 ROI、调用旧规则或场景、绘制结果图、汇总检测结果。 |
| 场景运行时 | `app/services/scenario_runtime.py` | 执行 VLM 直检或流程节点，保存节点输入、输出和耗时。 |
| 异步 Worker | `app/services/automation_worker.py` | 场景评测、提示词优化、YOLO 训练、异步 VLM 复核。 |
| Harness 插件运行时 | `app/harness/` | 将 OCR、相似度、颜色、分割、VLM 等能力与具体服务解耦。 |
| SMB 存储适配器 | `app/services/smb_storage.py` | 读取 UNC 图片、将结果图回写为源文件名加 `_result`。 |
| 算法服务 | `grounding_service/`、`dinov2_service/`、`ocr_service/`、`qwen_vl_service/`、`sam2_service/` | 提供定位、相似度、OCR、VLM、分割等模型能力。 |

### 2.2 关键端口与接口

| 地址 | 用途 |
| --- | --- |
| `http://<platform>:9010/` | 默认进入工艺配方库。 |
| `http://<platform>:9010/docs` | FastAPI 接口文档。 |
| `POST http://<platform>:9010/api/detect` | 供应商约定的生产检测接口，保持兼容。 |
| `http://<platform>:9010/api/v1/...` | 管理端 REST API。 |
| `http://<platform>:9021` | Grounding DINO 服务。 |
| `http://<platform>:9022` | DINOv2 相似度服务。 |
| `http://<platform>:9023` | Qwen3-VL / VLM 服务。 |
| `http://<platform>:9024` | PaddleOCR 服务。 |
| `http://<platform>:9025` | SAM2 服务。 |

服务地址由 `.env` 配置；模型服务可部署在本机或内网独立 GPU 服务器。

## 3. 功能模块

### 3.1 工艺配方库

入口：`/recipes/library`

- 业务匹配键：`line_code + material_code + process_code + camera_code + capture_index`。
- `project_name` 只做业务展示，不参与生产接口匹配。
- 配方按“配方家族 + 版本”保存。保存为草稿不会影响生产；只有发布后才成为 `POST /api/detect` 的可用版本。
- 支持详情、编辑、复制、测试、删除、查看历史版本和回滚。
- 编辑页面维护基础图、ROI、图像定位特征点、ROI 到已发布场景版本的绑定和输入变量映射。

### 3.2 ROI 与图像定位

- ROI 使用归一化坐标 `x_ratio/y_ratio/width_ratio/height_ratio` 存储，基础图尺寸保存在配方中。
- 特征点单独存入 `recipe_feature_anchors`，不作为检测 ROI，避免把定位目标误当作质量检测项。
- 生产检测时先尝试图像对齐，再按对齐后坐标裁剪 ROI；相关阈值由 `.env` 的 `IMAGE_ALIGNMENT_*` 配置控制。
- 对齐失败不会阻塞整次检测，而是记录原因后回退到原始坐标。高风险工位必须用现场样本验证特征点稳定性。

### 3.3 场景管理

入口：`/scenes/maintenance`、`/scenes/optimization`、`/scenes/evaluation`

场景有两种类型：

| 类型 | 说明 |
| --- | --- |
| `VLM_DIRECT` | 选择已配置的 OpenAI 兼容 VLM，使用提示词、变量、温度、Token 等参数直接判断图片。 |
| `WORKFLOW` | 使用画布保存开始、VLM、训练模型、图片裁剪、规则、Web API、条件、循环和结束节点。 |

流程节点当前支持 `START`、`VLM`、`VISION_MODEL`、`IMAGE_CROP`、`RULE`、`WEB_API`、`IF`、`LOOP`、`END`。发布前会验证开始/结束节点、连线完整性、不可达节点、环路，以及训练模型是否已发布且权重文件存在。

场景版本状态：

- `DRAFT`：可编辑，不能被生产 ROI 调用。
- `PUBLISHED`：不可直接编辑，可被 ROI 和外部场景接口调用。
- 新版本应从旧版本复制后修改、测试、再发布。

### 3.4 数据集管理

入口：`/datasets/maintenance`

| 数据集用途 | 适用功能 | 真值 / 标注 |
| --- | --- | --- |
| `TEST` | 场景评测、提示词优化 | 每张图片人工标记 `OK` 或 `NG`。 |
| `TRAIN` | YOLO 训练 | 目标检测框、目标分割多边形或图片分类标签。 |

训练数据可通过三种方式进入平台：

1. 上传原图后，在平台内框选目标并维护类别；
2. 上传本地文件夹中的原图后逐张在线标注；
3. 导入离线已标注 YOLO ZIP 包。该功能支持标准 `images/...` + `labels/...` 目录、同名 `.txt` 标签、`classes.txt` 或 `data.yaml` 类别表。导入时会校验类别编号顺序、坐标范围、重复图片和 ZIP 路径安全性，并转换为平台内部 JSON 标注。检测/分割样本缺少同名标签时作为空目标样本导入。

平台训练前固定对训练素材自动做 `70% / 20% / 10%` 的训练/验证/测试切分；离线 ZIP 中原有的 `train/val/test` 目录不会直接保留为最终切分，避免不同来源数据切分策略不一致。

### 3.5 模型管理与训练

入口：`/models/vlm`、`/models/registry`、`/models/training`

- **VLM 模型配置**：维护 OpenAI 兼容 `base_url`、`model_name`、API Key、是否启用思考、温度、最大 Token、超时和扩展参数；支持连接测试。
- **训练模型维护**：定义模型名称、任务类型、基础权重和类别。当前训练链路以 YOLO 的目标检测、目标分割、图片分类为主。
- **模型训练**：从训练数据集生成不可变快照，检查标注/类别/权重，等待 GPU 空闲显存满足预留阈值后开始。结果保存权重、训练曲线、混淆矩阵、指标和日志，随后生成草稿模型版本。
- 只有发布的模型版本可被流程中的 `VISION_MODEL` 节点引用。

### 3.6 质量运营、评测与复核

入口：`/operations/records`、`/operations/reports`

- 检测记录保存接口传参、配方、结果图、ROI 处理结果、场景执行记录和模型输出。
- 场景评测用 `TEST` 数据集的人工真值计算准确率、漏判（真值 `NG` 却输出 `OK`）和误判（真值 `OK` 却输出 `NG`）。
- 每个场景执行可以异步进入 VLM 复核；人工复判优先级高于 VLM 复核，且不会覆盖原始模型输出。
- 生产报表以 ROI 绑定的场景为维度聚合。生产接口本身只知道系统结果，**没有人工或下游质量真值时不能把 OK/NG 率等同为真实准确率**。

## 4. 生产 detect 接口

### 4.1 请求契约

接口：`POST /api/detect`

```json
{
  "line": "L4",
  "times": 1,
  "operation": "AS15",
  "materialCode": "CN000798",
  "image_paths": [
    "\\\\caxaprdfile.catl.com\\prd-file\\...\\AS15-CAMERA1PICTURE1-CN000798263700002-20260915134635.jpg",
    "\\\\caxaprdfile.catl.com\\prd-file\\...\\AS15-CAMERA2PICTURE1-CN000798263700002-20260915134649.jpg"
  ]
}
```

字段规则：

- `image_paths` 最多两张；空字符串路径会跳过。
- `line`、`materialCode`、`operation` 要么同时传入、要么都不传。
- 图片名中的 `CAMERA1PICTURE1` 会解析为 `camera=CAMERA1`、`times=1`；若显式传入 `camera` 或 `times`，平台会校验与图片名是否一致。
- `CAMERA1PICTURE1-` 后的条码会优先作为产品条码；未解析到时才使用可选 `sn`、`barcode` 或 `product_barcode`。
- 当没有完整业务参数时，平台退回按文件名匹配已发布配方。

### 4.2 运行流程

1. 清理空路径并解析相机、拍照次数和产品条码；
2. 按每张图片匹配对应的已发布工艺配方；
3. 若启用 SMB，下载图片到 `detection_results/<日期>/<配方>/<请求ID>/`；本地路径则复制或直接读取；
4. 两相机/两配方按 `PRODUCTION_IMAGE_PARALLELISM` 并发执行；同一图片中的 ROI 按 `PRODUCTION_ROI_PARALLELISM` 并发执行；VLM 调用按 `PRODUCTION_VLM_PARALLELISM` 限流；
5. 生成标注结果图，SMB 场景回写到原始目录，文件名为 `<原文件名>_result.<扩展名>`；
6. 保存 `detection_api_calls`、`detection_tasks`、`detection_item_results`、`scenario_executions` 等追溯记录并返回结果。

返回兼容顶层字段：

```json
{
  "code": 200,
  "message": "ok",
  "result": "OK",
  "image_paths": ["..._result.jpg"]
}
```

其中 `code/message` 代表接口执行状态；`result` 代表产品质量结果。模型或文件服务错误应返回 `ERROR`，不能伪装成产品 `NG`。

## 5. 异步任务与状态

`automation_jobs` 是统一持久化任务表，但页面已经按业务拆分展示：

| 任务类型 | 创建位置 | 执行内容 |
| --- | --- | --- |
| `SCENE_EVALUATION` | 场景评测 | 使用人工 OK/NG 测试集运行已发布场景并输出指标。 |
| `PROMPT_OPTIMIZATION` | 场景优化 | 在测试集上循环生成候选提示词，以实际准确率是否达到目标决定完成。 |
| `YOLO_TRAINING` | 模型训练 | GPU 资源满足后导出数据、训练并登记模型版本。 |
| `VLM_REVIEW` | 检测记录 / 异步复核 | 对一次场景执行进行一次复核。 |

主要状态：`QUEUED`、`WAITING_GPU`、`RUNNING`、`COMPLETED`、`FAILED`、`CANCELED`。本地 SQLite 模式下 Worker 每次认领一个任务，适合功能验证；Linux 生产环境迁移至 PostgreSQL/MySQL 后应单独部署 Worker 并使用事务/行锁保证多实例不重复认领。

## 6. 存储与备份

| 路径 / 数据 | 内容 | 备份要求 |
| --- | --- | --- |
| `data/vision_platform.db` | 本地 SQLite 数据库 | 停机或使用 SQLite 在线备份后备份。 |
| `uploads/` | 配方基础图、数据集素材、在线导入内容 | 与数据库一致性备份。 |
| `detection_results/` | 检测处理图、SMB 暂存图片 | 按保留策略归档。 |
| `embeddings/` | DINOv2 参考向量矩阵与清单 | 与 `reference_groups`、`reference_images` 一起备份。 |
| `data/training_runs/` | YOLO 训练日志、图表、产物 | 训练结果可追溯备份。 |
| `vision-models/` | 基础模型、发布权重或其挂载目录 | 不进 Git，使用模型仓库/NAS/制品库管理。 |
| `.env` | SMB、VLM、数据库等敏感配置 | 单独加密保管，严禁提交。 |

## 7. 环境配置与启动

### 7.1 必需准备

1. 从 `.env.example` 复制为 `.env`，填写实际服务地址、SMB 凭据、VLM 密钥和存储路径；
2. 在 Python 虚拟环境中安装 `requirements.txt`；
3. 将基础 YOLO 权重、DINOv2、Qwen3-VL、OCR、SAM2 等模型放入非 Git 的 `vision-models/` 或配置远程服务；
4. 按需启动模型服务，再启动平台。

### 7.2 启动命令

Windows：

```powershell
.\start.ps1
```

Linux：

```bash
./start.sh
```

启动时会执行数据库初始化、Harness 初始化和异步 Worker 启动。接口健康检查与模型连接状态应在生产切换前逐项验证。

### 7.3 MySQL 迁移说明

- 完整 DDL：`docs/mysql/vision_platform_mysql8.sql`。
- 字段说明：`docs/MySQL-Table-Structure.md`。
- 生成命令：`python scripts/generate_mysql_schema.py`。
- 新建 MySQL 8 数据库可在审阅后执行完整 DDL；迁移已有 SQLite/历史 MySQL 数据必须建立受控数据迁移和回滚方案。
- 应用当前默认 SQLite。切换 MySQL 时需要安装 SQLAlchemy 对应 MySQL 驱动，并将 `DATABASE_URL` 配置为类似 `mysql+pymysql://<user>:<password>@<host>:3306/vision_platform?charset=utf8mb4`。上线前需先在预生产验证字符集、时区、事务隔离、索引和备份恢复。

## 8. 安全与运维要求

- `.env`、SMB 密码、VLM API Key、模型权重、上传图片和训练数据禁止提交 Git。
- `TRUSTED_PROXY_HEADERS=true` 只能在可信反向代理之后启用，否则调用方 IP 可被伪造。
- SMB 路径必须位于 `SMB_SERVER_ROOT` 下；存储适配器会拒绝上级目录和越界路径。
- VLM API Key 可使用 `VLM_SECRET_KEY` 加密写入数据库；生产环境必须配置稳定的密钥，否则密文无法跨服务器解密。
- 生产部署应为平台、Worker、模型服务分别配置 systemd/容器健康检查、日志轮转、GPU 监控和自动重启。

## 9. 当前限制与风险

1. VLM 是复核证据而不是质量真值。高风险结果应保留人工复判入口并收集下游质量反馈。
2. 流程画布已经保存 DAG 连线和变量映射，但 V1 执行器仍以节点 `sort_order` 为主要执行顺序；复杂并行、循环体内动态分支需要继续做压力验证。
3. 生产准确率必须依赖人工复判或外部质量系统回传真值。仅用“原始检测结果与 VLM 复核结果一致”只能得到一致率，不能代表真实准确率。
4. 图像对齐只覆盖小范围平移和轻微旋转；治具大幅变化、曝光变化或特征点被遮挡时需要重新验证配方。
5. YOLO 离线导入兼容标准图片加标签 ZIP，但训练前仍应抽样检查类别、空目标比例、标注框和数据集泄漏。
6. 当前本地任务执行为单 Worker 设计。多 GPU、多机扩容前需要引入可靠队列、分布式锁、任务取消和资源隔离。
7. MySQL DDL 是由 ORM 自动生成的“新库建表基线”；不是已有数据库的在线迁移脚本。

## 10. 接手检查清单

1. 拉取 `develop`，复制 `.env.example` 并填写非敏感环境配置；
2. 确认 `start.ps1` / `start.sh` 可以启动平台；
3. 验证 `/docs`、`/api/v1/harness`、模型健康检查和管理端页面；
4. 用一套已发布配方调用 `POST /api/detect`，确认配方匹配、ROI、场景输出、结果图和检测记录；
5. 导入一份离线 YOLO ZIP，检查标注、类别和训练任务；
6. 创建并发布一个 VLM 场景、一个流程场景，确认 ROI 只能绑定已发布场景版本；
7. 跑一次场景评测和一次提示词优化，检查任务详情和输出；
8. 演练 SMB 读取与结果图回写；
9. 演练数据库、上传素材、Embedding、训练产物和模型权重的备份与恢复；
10. 正式上线前，确认模型服务容量、并发限制、错误码映射和 MES/相机软件联调结果。
