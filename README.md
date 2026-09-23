# Vision Platform

- 系统交接文档：`docs/Vision-Platform-System-Handover.md`
- OCR 服务迁移交接：`docs/OCR-Service-Handover.md`
- Word 交接文档：`docs/Vision-Platform-System-Handover.docx`
- 启动与接口 SOP：`docs/Vision-Platform-Operation-SOP.docx`
- 场景、数据集与模型模块：`docs/Scene-Dataset-Model-Architecture.md`
- 当前版本交接文档：`docs/Vision-Platform-Handover-V3.0.md`
- 历史 V2 交接文档：`docs/Vision-Platform-Handover-V2.0.md`
- MySQL 8 字段说明：`docs/MySQL-Table-Structure.md`
- MySQL 8 建表基线：`docs/mysql/vision_platform_mysql8.sql`

面向汽车电子装配错装、漏装、混装检测的工业视觉智能平台。第一阶段采用“工业配方 + 产品世界模型 + 可替换感知服务 + 规则决策”的结构。

## 架构

```text
相机软件 / 文件服务器
        |
        v
生产检测接口
        |
        v
配方路由（拉线 + 物料 + 工序 + 相机 + 拍照次数）
        |
        v
产品世界模型（对象、空间位置、期望状态、对象关系）
        |
        v
算法能力（DINOv2 / Grounding DINO / SAM2 / OCR / OpenCV / Qwen3-VL）
        |
        v
场景运行时与规则引擎
        |
        v
原始结果 / VLM 复核 / 人工复判
```

模型通过独立 HTTP 服务接入，平台只依赖能力接口，不绑定具体模型。

## Harness 风格插件技术栈

平台参考 [DeepSeek Harness](https://github.com/deepseek-ai/deepseek-harness) 的“能力皆插件 + 运行档案组合”思想，但**不直接引入其开发者预览运行时**，以保证产线 FastAPI 主流程稳定。

```text
FastAPI 平台内核
    -> Harness 运行档案（production / minimal / configuration）
        -> 插件清单（模型、OpenCV 规则、服务配置）
            -> 能力路由（OCR / 相似度 / 颜色 / 分割 / VLM）
                -> InspectionEngine 与既有 POST /api/detect
```

- 插件运行时：`app/harness/`，负责模型能力注册、替换和路由。
- 运行档案：`config/harness.json`，通过 `.env` 中的 `HARNESS_PROFILE` 选择。
- 诊断接口：`GET /api/v1/harness` 返回当前启用插件与能力路由。
- 详细说明：`docs/DeepSeek-Harness-Inspired-Architecture.md`。

模型替换只需要新增插件、选择能力路由和实现对应适配器；既有配方、ROI、规则和 `/api/detect` 接口不变。

## 已实现

- FastAPI 后端、SQLite 和 SQLAlchemy 2.0。
- 产品、工位、配方、ROI、检测项、参考向量和检测记录。
- 显式配方业务键：`line_code + material_code + process_code + camera_code + capture_index`。
- 配方按结构化参数优先匹配，缺省时兼容从图片文件名解析。
- 产品世界模型：ROI 自动映射为场景对象，并按相机和拍照次数保存多视角位置。
- 产品世界模型与 ROI 对象可视化编辑页面。
- 上传配方图片后由 Qwen3-VL 自动生成候选物体框，用户确认、移动、缩放或删除后再写入正式 ROI。
- DINOv2、Grounding DINO、SAM2、PaddleOCR、Qwen3-VL 服务接口预留。
- 场景管理：Dify 风格的专属场景设计器；直接 VLM 场景提供“参数配置 + 图片对话测试”双栏，流程场景提供可拖拽节点画布、端点连线、变量引用、版本发布和 ROI 已发布版本绑定。
- 数据集管理：TEST / TRAIN、图片或视频素材、OK/NG 真值与标注状态。
- 模型中心：OpenAI 兼容 VLM 配置、YOLO 检测/分割/分类模型定义、真实本地训练、版本发布和任务队列。
- 场景评测、提示词优化与异步独立 VLM 复核任务；评测、优化和模型训练任务分别在对应功能页面以表格查看，避免混在同一个队列中。
- 线束采用 Grounding DINO 粗定位、SAM2 像素级分割和橙色 HSV 快速分割融合。
- Qwen3-VL 4B 本地 4-bit 测试服务。
- DINOv2 低置信度区间自动触发 Qwen3-VL 复核，异常时按安全策略判定。
- 生产相似度检测复用已保存的标准 Embedding，实测 ROI 每次只编码一次。
- 标准 Embedding 使用 FP16 保存；同一 ROI 的活动基准合并为一个矩阵文件，按行号读取。
- 向量目录按拉线、物料、工序、相机、拍照次数、配方和 ROI 分层，数据库只保存相对路径。
- 相似度默认使用 `65% × Top1 + 35% × Top3均值` 的稳健分数，避免单张异常基准决定结果。
- 候选基准后台采集已停用；历史候选与参考文件保留但不会再自动新增。
- 已发布 YOLO 模型可由工作流“模型检测”节点直接执行；节点支持数量期望、类别期望、置信度和 IoU 参数。

## 目录

```text
app/
  api/                    API 路由
  core/                   配置
  db/                     数据库会话、初始化和轻量升级
  models/                 配方、检测、场景、数据集和世界模型 ORM
  services/               检测执行、场景运行时、自动化 worker 与算法客户端
  harness/                插件运行时、插件清单和能力路由
  static/                 管理页面静态资源
  templates/              管理页面模板
config/                   配置说明
scripts/                  初始化、启动、下载和测试脚本
tests/                    单元测试
dinov2_service/           DINOv2 独立服务
grounding_service/        Grounding DINO 独立服务
qwen_vl_service/          Qwen3-VL 独立服务
sam2_service/             SAM2.1 线束分割独立服务
vision-models/            本地模型文件，不纳入 Git
test_images/              测试图片
embeddings/               分层的 ROI 向量矩阵和清单
```

## 平台启动

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
python3.11 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
cp .env.example .env
./start.sh
```

访问：

- 管理页面：`http://127.0.0.1:9010`
- API 文档：`http://127.0.0.1:9010/docs`
- 算法状态：`http://127.0.0.1:9010/api/v1/algorithms/status`

## YOLO 训练与流程调用

1. 在“模型中心 → 训练模型维护”创建 YOLO 目标检测、分割或分类模型，并维护识别类别。
2. 在“数据集管理”创建匹配的 `TRAIN` 图片数据集。训练素材可选择以下任一方式维护：
   - 在平台上传原图并进行在线标注：目标检测使用 `{"boxes":[{"label":"harness","x":0.1,"y":0.2,"width":0.3,"height":0.2}]}`；目标分割使用 `{"segments":[{"label":"harness","points":[[0.1,0.2],[0.4,0.2],[0.4,0.5]]}]}`；分类使用 `{"label":"harness"}`。
   - 导入离线已标注的 YOLO ZIP 包：压缩包可使用标准 `images/...` 与 `labels/...` 目录，也可使用图片与同名 `.txt` 标签；类别优先读取 `classes.txt` 或 `data.yaml`。平台会校验类别编号顺序并将数据转换为内部标注格式。检测/分割图片缺少同名标签时按“空目标样本”导入；训练时仍由平台自动按 70% / 20% / 10% 切分，不沿用压缩包中的目录切分。
3. 每个训练样本完成标注后，在“模型训练”提交任务。平台先校验类别、标注、图片路径，再固定为 70% / 20% / 10% 切分。
4. Worker 在可用显存满足条件时导出 YOLO 数据集、运行本地 Ultralytics 训练，并把 `best.pt`、曲线和指标登记为草稿模型版本。训练产物保存在 `data/training_runs/`。
5. 检查指标后在模型卡片上发布该草稿版本；“场景维护 → 模型检测节点”只允许选择权重文件存在的已发布版本。

训练过程不会自动发布模型，也不会让草稿权重进入生产配方。

离线服务器请提前将选择的基础权重（例如 `yolo11n.pt`、`yolo11n-seg.pt` 或 `yolo11n-cls.pt`）放入 `vision-models/`；训练器会优先读取该目录，避免运行时下载。

## 配方规则

推荐配方编码：

```text
{LINE}_{MATERIAL}_{PROCESS}_{CAMERA}_P{CAPTURE_INDEX}
```

例如：

```text
L01_PDU001_AS10_CAM01_P01
```

结构化调用优先：

```json
{
  "sn": "SN202608010001",
  "line_code": "L01",
  "material_code": "PDU001",
  "process_code": "AS10",
  "camera_code": "CAM01",
  "capture_index": 1,
  "image_paths": ["D:/vision-images/product_001.jpg"]
}
```

兼容调用：

```json
{
  "sn": "SN202608010001",
  "image_paths": [
    "D:/vision-images/L01_PDU001_AS10_CAM01_P01_001.jpg"
  ]
}
```

匹配优先级为：结构化参数、图片名称。生产接口为 `POST /api/detect`，顶层返回继续兼容 `code`、`message`、`result` 和 `image_paths`。

## 产品世界模型与复核策略

每个配方 ROI 会同步为产品世界模型中的场景对象。一个对象可在不同相机和拍照次数下拥有独立视角坐标，视角键格式为 `CAMERA:Pnn`。对象保留类型、定位方式、期望状态和感知能力，ROI 仍作为生产检测的确定性执行区域。

DINOv2 相似度结果仅在配置的边界区间内触发 Qwen3-VL：

```text
明确通过或明确失败 -> 直接使用 DINOv2 结果
处于低置信度边界 -> 裁剪 ROI 后调用 Qwen3-VL 复核
VLM 返回 UNCERTAIN、格式错误或服务不可用 -> 按 NG 安全降级
```

复核开关、上下限和提示词均在 ROI 对象编辑页面中配置。Qwen3-VL 默认注册为 `FALLBACK_ONLY`，不会成为每个检测点的必经链路。

配方配置阶段采用“自动发现优先、人工确认兜底”的方式：

```text
上传产品图片
-> Qwen3-VL 解析可检测物体和候选框
-> 页面显示紫色虚线候选框
-> 用户移动、缩放、删除或确认
-> 确认后的对象写入产品世界模型并生成正式 ROI
-> 自动发现服务不可用时仍可手动画框
```

## Qwen3-VL 本地测试

当前桌面机器检测到 RTX 4060 Ti 8GB，建议使用独立环境和 4-bit 量化：

```powershell
python -m venv .venv-qwen
.\.venv-qwen\Scripts\python.exe -m pip install torch torchvision --index-url https://download.pytorch.org/whl/cu121
.\.venv-qwen\Scripts\python.exe -m pip install -r qwen_vl_service\requirements.txt
.\.venv-qwen\Scripts\python.exe scripts\download_qwen_vl.py
.\.venv-qwen\Scripts\python.exe scripts\run_qwen_vl.py
```

默认使用 ModelScope 国内源下载；如需 Hugging Face，可增加参数
`--source huggingface`。

另开终端测试：

```powershell
.\.venv\Scripts\python.exe scripts\test_qwen_vl.py --image test_images\test.jpeg
```

模型服务端口为 `9023`。平台代理接口为 `POST /api/v1/algorithms/vlm/judge`。只应向 VLM 传入裁剪后的 ROI 小图，并将它用于复杂关系或低置信度复核。

## SAM2 线束分割

SAM2.1 Hiera Small 运行在 `9025`。当前 RTX 4060 Ti 显存已被 Qwen 与 Grounding 占用，默认使用 CPU，避免服务同时运行时 OOM：

```powershell
git clone --depth 1 https://github.com/facebookresearch/sam2.git third_party\sam2
$env:SAM2_BUILD_CUDA="0"
.\.venv\Scripts\python.exe -m pip install --no-build-isolation -e third_party\sam2
.\start-sam2.ps1
```

自动解析链路为：

```text
Grounding DINO 生成黑色、灰色、橙色和低压线束粗框
-> SAM2 根据粗框生成像素掩膜
-> 与橙色 HSV 快速分割去重合并
-> 工作台叠加青色 SAM2 轮廓和橙色颜色轮廓
```

Qwen3-VL 需要较新的 Transformers；官方模型说明要求 `transformers>=4.57.0`，服务依赖已单独放在 `qwen_vl_service/requirements.txt`。

## 参考向量存储

每个 ROI 活动基准集只读取一个 `embeddings.npy`，矩阵每一行对应一张参考图；`manifest.json` 保存行号与参考图 ID 的映射。示例：

```text
embeddings/LINE_LINE01/MATERIAL_MAT001/PROCESS_OP20/
  CAMERA_CAMERA1/SHOT_01/RECIPE_00000001/ROI_00000012/SET_V0003/
    embeddings.npy
    manifest.json
```

旧版单图 `.npy` 仍可读取，不会自动删除。迁移前先预览，确认后再应用：

```powershell
.\.venv\Scripts\python.exe scripts\migrate_reference_embeddings.py
.\.venv\Scripts\python.exe scripts\migrate_reference_embeddings.py --apply
```

Linux 可把 `EMBEDDING_STORAGE_ROOT` 指向独立数据盘，例如 `/data/vision-platform/embeddings`。

当参考图不足 3 张时会自动使用实际数量；只有 1 张时稳健分数等于 Top1，不改变现有单基准配方行为。规则测试结果会同时返回 Top1、Top-K 均值、离散度和最终稳健分数。

## 离线静态资源

前端不依赖公网 CDN。Bootstrap 5.3.7 的 CSS、Bundle JS 和许可证已固定保存在：

```text
app/static/vendor/bootstrap/5.3.7/
```

部署到无网服务器时必须保留整个 `app/static` 目录。页面通过 FastAPI 的 `/static` 路由加载 Bootstrap、平台 CSS 和 JavaScript，无需访问互联网。

## 下一阶段

1. 完成配方版本复制、测试、发布、归档和审计。
2. 增加检测执行计划，对 DINOv2、OCR、颜色规则进行批处理。
3. 将 SQLite 迁移为 PostgreSQL，并将标准图迁移到 NAS 或 MinIO。
4. 增加基准集容量、重复率和Embedding健康状态统计。

## Detect 接口测试

公开检测接口支持以下五个配方参数：

```text
line            拉线
materialcode    物料号
operation       工序
camera          相机，例如 CAMERA1
picture         第几次拍照，例如 1
```

当只传 `line`、`materialcode`、`operation` 时，平台会从图片名称中的
`CAMERA数字PICTURE数字` 自动补全相机和拍照次数。

使用固定测试图片调用接口：

```powershell
.\.venv\Scripts\python.exe scripts\test_detect.py `
  --line LINE01 `
  --materialcode MAT001 `
  --operation OP20
```

测试脚本默认使用：

```text
C:\Users\Administrator\Desktop\vision-platform\test_images\ASSY-CAMERA1PICTURE1.png
```
