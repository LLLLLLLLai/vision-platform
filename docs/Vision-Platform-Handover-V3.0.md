# Vision Platform V3.0 交接手册

> 更新日期：2026-09-23
> 代码分支：`develop`
> 适用范围：汽车电子装配的错装、漏装、混装检测，以及基于 VLM / YOLO 的场景设计、评测与训练。
> 本文不包含模型权重、产线图片、SMB 密码或 VLM API Key；这些内容只保存在服务器的 `.env`、NAS 或模型制品库中。

---

## 1. 系统定位

平台把“**在哪检查**”和“**如何检查**”分开管理：

```text
相机软件 / MES / 外部业务系统
        │
        ├── 生产检测：POST /api/detect
        └── 单场景调用：POST /api/v1/scenarios/invoke/{scene_code}
                    │
                    ▼
工艺配方（拉线 + 物料 + 工序 + 相机 + 拍照次数）
                    │
                    ▼
ROI / 特征点对齐（在哪检查）
                    │
                    ▼
已发布检测场景（如何检查）
   ├── VLM 直接检测
   └── 流程编排：VLM、YOLO、裁剪、规则、Web API、条件、循环
                    │
                    ▼
结果图、场景执行记录、异步复核、人工复判、报表
```

平台**不控制相机、PLC、运动轴或光源**。相机软件负责拍照和上传图片；平台负责配方匹配、ROI/场景执行、结果归档和对外返回。

---

## 2. 技术与目录

| 项目 | 当前实现 |
| --- | --- |
| 后端 | Python 3.11、FastAPI、SQLAlchemy 2.0、Pydantic 2 |
| 管理端 | Jinja2 + 本地 Bootstrap 5.3 + 原生 JavaScript/CSS，可离线使用 |
| 本地数据库 | SQLite（默认） |
| 生产数据库 | MySQL 8 / PostgreSQL 均可接入；当前提供 MySQL 8 建表基线 |
| 异步任务 | 平台内持久化 `automation_jobs` + 单 Worker；生产建议拆为独立 Worker |
| 模型接入 | OpenAI 兼容 VLM、YOLO、PaddleOCR、DINOv2、Grounding DINO、SAM2 |

```text
app/
  api/                 REST 接口：配方、检测、场景、数据集、模型、任务
  models/              SQLAlchemy 表模型
  services/            检测引擎、场景运行时、SMB、训练、异步 Worker
  harness/             能力插件与运行档案
  static/              本地 CSS / JS / Bootstrap
  templates/           管理端页面
  web/                 页面路由
config/                Harness 配置
scripts/               初始化、启动、模型服务和测试脚本
docs/                  交接、SOP、SMB 与 MySQL 文档
vision-models/         基础权重、已发布权重（不提交 Git）
uploads/               配方图、数据集上传内容（不提交 Git）
detection_results/     检测中间图、结果图（不提交 Git）
data/training_runs/    YOLO 训练产物（不提交 Git）
embeddings/            参考向量（不提交 Git）
```

---

## 3. 页面与功能模块

### 3.1 工艺配方库

入口：`/recipes/library`

- 业务匹配键：`line_code + material_code + process_code + camera_code + capture_index`。
- `project_name` 为展示字段，不参与 `POST /api/detect` 匹配。
- 一个配方由“配方家族 + 版本”组成：保存为草稿不影响生产；只有 `PUBLISHED` 版本可被生产调用。
- 支持：创建、详情、编辑、复制、测试、删除、查看历史、回滚。
- 编辑页维护：基础图、ROI、特征点、ROI 关联的已发布场景和场景输入字段值。

### 3.2 ROI、特征点与图像对齐

- ROI 使用相对坐标保存，适应不同分辨率。
- 特征点独立于 ROI；生产检测先识别特征点，再计算 ROI 的实际位置，用于补偿小范围平移和轻微旋转。
- ROI 只可关联**已发布**的检测场景版本，避免草稿逻辑进入生产。
- 特征点对齐失败时会记录原因并回退到原始 ROI；上线前必须使用现场样本确认特征点稳定性。

### 3.3 场景管理

入口：`/scenes/maintenance`、`/scenes/optimization`、`/scenes/evaluation`

| 类型 | 说明 |
| --- | --- |
| `VLM_DIRECT` | 选择已配置的 OpenAI 兼容 VLM，以提示词、场景变量、温度、最大 Token 和扩展参数直接判断图片。 |
| `WORKFLOW` | 通过画布组织节点；当前支持开始、VLM、训练模型、图片裁剪、规则、Web API、条件、循环、结束。 |

场景版本状态：

- `DRAFT`：可编辑、可测试，不能被生产配方或外部系统调用。
- `PUBLISHED`：不可直接编辑，可被 ROI 和场景 API 调用。
- `ARCHIVED`：发布新版本后旧版本自动归档；历史执行记录仍指向原版本。

流程设计器功能：

- 左侧节点库、中间画布、右侧节点配置；支持拖拽节点和端点连线。
- 输入/输出参数按“键 + 值”维护；可引用开始节点输入和上游节点输出。
- VLM 提示词变量可使用 `{{ input.field }}`、`{{ nodes.node_key.output }}`；界面显示为可删除的变量标签。
- 测试流程会显示每个节点的输入、输出、最终结果和耗时。

### 3.4 数据集管理

入口：`/datasets/maintenance`

| 用途 | 说明 |
| --- | --- |
| `TEST` | 场景评测和提示词优化。人工为每张图片标记 `OK` 或 `NG`，作为真实标签。 |
| `TRAIN` | YOLO 训练。支持目标检测框、分割多边形和分类标签。 |

- 支持在线框选、多类别标注、删除重画、批量/文件夹上传。
- 支持导入离线已标注 YOLO ZIP：`images/` + `labels/`、同名 `.txt`、`classes.txt` 或 `data.yaml`。
- 训练前自动以 `70% / 20% / 10%` 进行训练、验证、测试切分。

### 3.5 模型管理

| 页面 | 作用 |
| --- | --- |
| `/models/vlm` | 配置 OpenAI 兼容 VLM：服务地址、模型名、API Key、思考模式、温度、Token、超时和扩展参数；可测试连接。 |
| `/models/registry` | 定义训练模型名称、任务类型、类别和基础权重。当前训练链路以 YOLO 检测、分割、分类为主。 |
| `/models/training` | 创建训练任务，等待可用显存，导出数据、训练、记录曲线/混淆矩阵/指标，并生成草稿模型版本。 |

只有权重文件存在的**已发布模型版本**可被流程中的“训练模型”节点选择。

### 3.6 质量运营与异步任务

入口：`/operations/records`、`/operations/reports`

- 检测记录保存输入参数、纠正后的产品条码、配方、原图/结果图、ROI 结果、场景执行记录和模型输出。
- 记录详情可查看配方基础图、实际图、标注结果图及各 ROI 的处理情况。
- 报表按 ROI 关联的场景聚合调用次数、通过率、复核覆盖率和人工确认准确率。
- 原始检测结果不会被复核覆盖；人工复判优先级高于 VLM 复核。

异步任务表为 `automation_jobs`，页面按业务模块分别展示：

| 任务 | 入口 | 内容 |
| --- | --- | --- |
| `SCENE_EVALUATION` | 场景评测 | 对 TEST 数据集运行已发布场景，计算准确率、漏判和误判。 |
| `PROMPT_OPTIMIZATION` | 场景优化 | 用测试集循环生成候选提示词；只输出候选，不会自动覆盖已发布场景。 |
| `YOLO_TRAINING` | 模型训练 | 显存满足后导出数据、训练并登记草稿模型版本。 |
| `VLM_REVIEW` | 记录复核 / 场景 API | 对一次执行进行一次独立复核。 |

---

## 4. 对外接口

### 4.1 生产检测接口（兼容外围系统）

```text
POST http://<platform-host>:9010/api/detect
```

典型请求：

```json
{
  "line": "L4",
  "times": 1,
  "operation": "AS15",
  "materialCode": "CN000798",
  "image_paths": [
    "\\\\server\\share\\AS15-CAMERA1PICTURE1-CN000798263700002-20260915134635.jpg",
    "\\\\server\\share\\AS15-CAMERA2PICTURE1-CN000798263700002-20260915134649.jpg"
  ]
}
```

规则：

1. `image_paths` 最多两张，空字符串路径会跳过。
2. 从 `CAMERA1PICTURE1` 解析相机和拍照次数；从其后的条码解析产品条码。
3. 使用 `line + materialCode + operation + camera + times + PUBLISHED` 匹配工艺配方。
4. 两张图片、同图多个 ROI 使用受控并发处理；VLM 并发量由 `.env` 限制。
5. 结果图写回原图同级目录，文件名追加 `_result`；SMB 模式下通过 SMB 回写。

兼容返回：

```json
{
  "code": 200,
  "message": "success",
  "result": "OK",
  "image_paths": ["..._result.jpg"]
}
```

`code/message` 表示接口状态；`result` 表示业务结果 `OK`、`NG` 或 `ERROR`。

详细 SMB 说明见：`docs/Detect-接口与SMB集成说明.md`。

### 4.2 已发布场景接口（新增）

每个已发布检测场景均有一个稳定接口：

```text
GET  /api/v1/scenarios/invoke/{scene_code}   # 获取调用契约
POST /api/v1/scenarios/invoke/{scene_code}   # 执行当前已发布版本
```

设计器右上角的“**接口调用**”按钮可直接查看、复制接口地址、参数和返回示例。

请求格式：

```json
{
  "request_id": "scene-call-001",
  "image_path": "C:/vision-share/sample.jpg",
  "inputs": {
    "ocr_text": "FUS"
  },
  "enqueue_review": false
}
```

说明：

- `image_path` 必须是**平台服务器可读**的本地挂载路径或 SMB 临时下载路径，不能仅是调用方电脑上的私有路径。
- `inputs` 字段来自 VLM 场景的“ROI 可传入参数”或流程场景开始节点定义；字段标记为必填时不可省略。
- 对 VLM 场景，如需将 `ocr_text` 作为强制判定依据，请在提示词中引用 `{{ input.ocr_text }}` 并写明比对规则。
- 接口始终调用该场景当前 `PUBLISHED` 版本，外部系统不能调用草稿或归档版本。
- 正常执行（包括业务 `NG` 和 `UNCERTAIN`）返回 `code: 0`；场景运行失败返回 `code: 5001`。
- `enqueue_review: true` 会为本次执行创建异步 VLM 复核任务。

生产环境应在 `.env` 中设置：

```dotenv
SCENE_API_KEY=<随机长密钥>
```

调用时额外传：

```text
X-Scene-API-Key: <同一个密钥>
```

未设置该配置时，仅适用于本地或受控内网测试。

---

## 5. 配置与存储

### 5.1 `.env` 配置原则

从 `.env.example` 复制创建 `.env`；`.env` 永远不提交 Git。常用配置：

| 配置 | 用途 |
| --- | --- |
| `APP_HOST` / `APP_PORT` | 平台监听地址，默认端口 `9010`。 |
| `DATABASE_URL` | SQLite 或 MySQL/PostgreSQL 连接串。 |
| `SMB_ENABLED`、`SMB_SERVER_ROOT`、`SMB_USERNAME`、`SMB_PASSWORD` | 生产图片下载和结果图回写。 |
| `SCENE_API_KEY` | 新场景外部接口认证。 |
| `VLM_SECRET_KEY` | 加密数据库中保存的 VLM API Key。 |
| `GROUNDING_SERVICE_URL` 至 `SAM2_SERVICE_URL` | 各模型服务地址。 |
| `PRODUCTION_IMAGE_PARALLELISM` | 两相机/多图并行上限。 |
| `PRODUCTION_ROI_PARALLELISM` | 单图 ROI 并行上限。 |
| `PRODUCTION_VLM_PARALLELISM` | VLM 全局并发上限，应与实例数量一致。 |
| `IMAGE_ALIGNMENT_*` | 特征点对齐阈值。 |

### 5.2 需要备份的数据

| 位置 | 内容 | 备份要求 |
| --- | --- | --- |
| `data/vision_platform.db` | SQLite 数据库 | 停机或在线备份后复制；不要直接复制正在写入的文件。 |
| `uploads/` | 配方图、数据集素材 | 与数据库一并备份。 |
| `detection_results/` | 原图暂存、ROI 图、结果图 | 按工厂保留策略归档。 |
| `data/training_runs/` | 训练日志、指标、权重 | 保留可追溯训练证据。 |
| `embeddings/` | DINOv2 参考向量 | 与相应数据库记录同时备份。 |
| `vision-models/` | 基础/发布模型权重 | 放 NAS、制品库或模型仓库；不进入 Git。 |
| `.env` | 密钥与服务配置 | 加密保管、单独备份。 |

MySQL 8 建表基线：`docs/mysql/vision_platform_mysql8.sql`；字段说明：`docs/MySQL-Table-Structure.md`。

---

## 6. 启动与停止

### 6.1 首次安装（Windows）

```powershell
cd C:\vision-platform
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
```

完成 `.env` 中的数据库、SMB、VLM 和模型服务地址配置后启动：

```powershell
.\start.ps1
```

`start.ps1` 会先执行数据库初始化，再运行平台。访问：

```text
管理页面：http://127.0.0.1:9010/
接口文档：http://127.0.0.1:9010/docs
平台健康：http://127.0.0.1:9010/api/v1/health
模型状态：http://127.0.0.1:9010/api/v1/algorithms/status
```

### 6.2 RHEL 8.3 / Linux 启动

建议使用 Python 3.11，并通过 `systemd` 管理平台和 Worker 进程。首次安装：

```bash
cd /opt/vision-platform
python3.11 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements.txt
cp .env.example .env
chmod +x start.sh
./start.sh
```

直接运行时，`start.sh` 会先初始化数据库再启动 Uvicorn。正式生产不要使用 `APP_DEBUG=true`；应由 systemd 或容器负责重启、日志轮转、权限和健康检查。

### 6.3 可选模型服务启动

平台可在模型服务未全部启动时运行，但对应场景会返回错误或不可用。常用本地脚本：

```powershell
.\.venv\Scripts\python.exe scripts\run_ocr.py        # 9024，PaddleOCR
.\.venv\Scripts\python.exe scripts\run_grounding.py  # 9021，Grounding DINO
.\.venv\Scripts\python.exe scripts\run_dinov2.py     # 9022，DINOv2
.\.venv\Scripts\python.exe scripts\run_qwen_vl.py    # 9023，本地 Transformers Qwen 服务
.\.venv\Scripts\python.exe scripts\run_sam2.py       # 9025，SAM2
```

如果使用 Docker/vLLM 的 Qwen3-VL，应保证其 OpenAI 兼容地址可访问，例如：

```text
http://<vlm-host>:8000/v1/models
http://<vlm-host>:8000/v1/chat/completions
```

随后在“VLM 模型配置”中维护 `base_url`、`model_name` 和认证方式，并执行“测试连接”。平台不要求 VLM 必须部署在本机。

### 6.4 停止和日志

- 前台运行：在启动终端按 `Ctrl+C`。
- systemd：使用 `systemctl stop <service-name>`。
- 平台日志、模型服务日志应通过 systemd/journald 或容器日志统一收集；本地临时日志目录为 `logs/`。
- 模型服务的“启动、停止、日志和健康状态”可在平台模型相关页面排查；生产环境仍需 OS 级守护。

---

## 7. 日常操作流程

### 7.1 新增一个 VLM 检测场景

1. 在“场景维护”创建 `VLM_DIRECT` 场景；
2. 在设计页选择已配置 VLM，编辑提示词和场景输入字段；
3. 上传测试图，查看检测输出；
4. 必要时创建 TEST 数据集并执行场景评测；
5. 发布场景版本；
6. 在工艺配方 ROI 中选择该已发布版本，并填写 ROI 对应场景字段值；
7. 在设计器点击“接口调用”获得独立场景 API，或通过 `/api/detect` 进行生产调用。

### 7.2 新增一个 YOLO 场景

1. 创建 TRAIN 数据集，上传图片并维护类别/标注，或导入离线 YOLO ZIP；
2. 在“训练模型维护”创建检测、分割或分类模型定义，配置基础权重和类别；
3. 在“模型训练”创建任务，查看显存等待、训练曲线、混淆矩阵和指标；
4. 确认效果后发布训练模型版本；
5. 创建/编辑流程场景，添加“训练模型”节点并引用已发布版本；
6. 如果后续需使用目标框，增加“图片裁剪”节点，将上游 `bbox` 传入；
7. 完整测试后发布场景，再绑定 ROI 或调用场景 API。

### 7.3 修改生产配方

1. 在工艺配方库点击“编辑”；已发布配方先创建草稿版本；
2. 修改 ROI、特征点、场景绑定或场景输入字段值；
3. 使用配方测试，确认 ROI 对齐、场景输入和结果图；
4. 保存草稿只保留编辑结果，不影响生产；
5. 点击发布后，新版本成为 `POST /api/detect` 的匹配版本；旧版本保留给历史检测记录追溯。

---

## 8. 验证与故障排查

### 8.1 交付前验证

```powershell
cd C:\vision-platform
node --check app\static\js\scene_designer.js
.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_*.py" -q
```

还应人工验证：

1. 管理端可打开，侧边菜单和场景设计器正常；
2. 创建并发布一个 VLM 场景和一个流程场景；
3. 设计器“接口调用”弹窗可显示契约；
4. 用 `POST /api/v1/scenarios/invoke/{scene_code}` 实际调用已发布 VLM、流程场景；
5. 用生产图片调用 `POST /api/detect`，确认配方匹配、SMB、结果图和检测记录；
6. 创建一次数据集评测、一次提示词优化、一次 YOLO 训练任务；
7. 检查数据库、上传图、训练产物和模型权重备份。

### 8.2 常见问题

| 现象 | 优先排查 |
| --- | --- |
| 场景接口返回 404 | 确认 `scene_code`、平台已重启、该场景存在。 |
| 场景接口返回 409 | 场景未发布、已停用，或发布版本异常。 |
| 场景接口返回 422 | 对照“接口调用”弹窗，检查必填字段和字段名。 |
| `code: 0` 但业务结果 `UNCERTAIN` | 接口执行成功；模型未能确认业务结果。检查图片、提示词、ROI 和模型能力。 |
| VLM 400/404 | 检查 VLM `base_url` 是否应带 `/v1`，模型名是否和 `/v1/models` 返回一致。 |
| VLM 超时 | 检查图片大小、`max_tokens`、并发量、GPU 显存和 VLM 实例数。 |
| `WAITING_GPU` | 训练任务等待可用显存；检查 GPU 进程、预留显存与训练参数。 |
| `/api/detect` 找不到配方 | 核对 `line`、`materialCode`、`operation`、图片名中的相机/拍照次数，以及配方是否已发布。 |
| SMB 读写失败 | 检查 `.env`、服务运行账户、共享目录权限、UNC 路径是否位于 `SMB_SERVER_ROOT` 下。 |
| ROI 偏移 | 检查特征点框、基础图、`IMAGE_ALIGNMENT_*` 参数和工装/相机变化。 |

---

## 9. 生产风险与后续建议

1. **真实准确率需要真值**：生产 `OK/NG` 率不等于准确率。应结合人工复判、下游质量结果或 TEST 数据集真值统计漏判和误判。
2. **VLM 是辅助能力**：VLM 可解释复杂情况，但不能作为唯一质量真值。高风险项应使用明确规则、专用模型或人工复判闭环。
3. **场景 API 要启用认证**：生产环境必须设置 `SCENE_API_KEY`，并在反向代理、防火墙层限制来源地址。
4. **模型与数据不进入 Git**：权重、训练图、产品图、`.env` 需要使用 NAS、对象存储或制品库，并建立权限和备份策略。
5. **Worker 扩容前要压测**：当前本地模式适合单 Worker 功能验证；多 GPU/多机部署前需要数据库锁、可靠队列、任务取消、GPU 资源隔离和幂等控制。
6. **配方与场景均需版本化**：生产调用只读取已发布版本；不要直接覆盖已发布定义，避免历史记录无法复现。

---

## 10. 交接检查清单

- [ ] 已从 `develop` 拉取源码，并确认 `.env` 未提交。
- [ ] 已确认模型权重、数据集、检测图片、Embedding 和训练产物的实际存储位置。
- [ ] 已验证 `start.ps1` / `start.sh` 和 `/api/v1/health`。
- [ ] 已验证每个模型服务的地址、模型名称和连接测试。
- [ ] 已设置 `SCENE_API_KEY`、SMB 和 `VLM_SECRET_KEY` 等生产配置。
- [ ] 已用真实样本验证 `/api/detect`、结果图回写和检测记录。
- [ ] 已用已发布 VLM 场景和流程场景分别验证场景 API。
- [ ] 已验证至少一套场景评测、提示词优化和 YOLO 训练流程。
- [ ] 已演练 SQLite/MySQL、上传素材、模型权重和 `.env` 的备份恢复。

相关补充文档：

- `docs/Detect-接口与SMB集成说明.md`
- `docs/OCR-Service-Handover.md`
- `docs/MySQL-Table-Structure.md`
- `docs/Scene-Dataset-Model-Architecture.md`
- `docs/DeepSeek-Harness-Inspired-Architecture.md`
