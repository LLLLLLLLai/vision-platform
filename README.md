# Vision Platform

## Production image filename convention

`POST /api/detect` selects a published recipe for every image by parsing its
filename. The recommended filename is:

```text
{line_code}-{material_code}-{process_code}-{camera_code}-P{capture_index}_{timestamp}.jpg
```

Example:

```text
LINE01-MAT001-OP20-CAM01-P1_20260729103000123.jpg
```

Hyphens, underscores, dots, and spaces are treated as equivalent separators.
The filename may also contain the complete recipe code. Every image must match
one published recipe. Images matched to the same recipe are processed together;
results from different recipes are aggregated into one `OK`, `NG`, or `ERROR`
response.

工业视觉检测平台基础工程，面向固定相机、固定工装下的错装、漏装、混装检测。

核心抽象：

```text
产品 → 工位 → 配方 → ROI → 检测项 → 算法能力 → 规则 → OK/NG
```

当前版本提供：

- FastAPI 应用入口与健康检查
- SQLAlchemy 2.0 + SQLite 数据库骨架
- 产品、工位、配方、ROI、检测项、算法配置基础模型
- Bootstrap 管理首页
- DINOv2、Grounding DINO、PaddleOCR 服务调用占位
- 数据、上传、Embedding、检测结果和日志目录
- 数据库初始化与本地启动脚本

## 目录

```text
app/
  api/                 API 路由
  core/                配置
  db/                  数据库会话与初始化
  models/              ORM 模型
  schemas/             接口数据结构
  services/            业务与算法服务
  static/              前端静态资源
  templates/           Jinja2 页面
config/                配置说明
data/                  SQLite 数据库
scripts/               初始化和启动脚本
vision-models/         本地模型目录（保留）
grounding_service/     Grounding DINO 独立服务（保留）
dinov2_service/        DINOv2 独立服务（保留）
test_images/           测试图片（保留）
```

## Windows 快速启动

```powershell
cd C:\Users\Administrator\Desktop\vision-platform
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
python scripts\init_db.py
python scripts\run.py
```

如果系统没有可用的 `python` 命令，请先安装 Python 3.11 或 3.12，并在安装器中勾选
“Add Python to PATH”。

浏览器打开：

- 管理首页：`http://127.0.0.1:9010`
- API 文档：`http://127.0.0.1:9010/docs`
- 健康检查：`http://127.0.0.1:9010/api/v1/health`

## Linux 快速启动

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python scripts/init_db.py
python scripts/run.py
```

## 模型服务约定

平台不直接加载大型模型，只通过 HTTP 调用独立推理服务：

- Grounding DINO：`GET /health`、`POST /v1/localize`
- DINOv2：`GET /health`、`POST /v1/embedding`、`POST /v1/similarity`
- PaddleOCR：沿用现有服务，地址通过环境变量配置

GPU 模型进程应保持单进程加载，避免多个 Web Worker 重复占用显存。

## DINOv2 Base

Windows + NVIDIA GPU 推荐先安装 CUDA 12.1 版 PyTorch：

```powershell
python -m pip install torch torchvision --index-url https://download.pytorch.org/whl/cu121
python -m pip install -r dinov2_service\requirements.txt
python scripts\download_dinov2.py
python scripts\run_dinov2.py
```

服务默认读取 `vision-models/dinov2-base`，并监听 `9022` 端口。

测试：

```powershell
python scripts\create_test_images.py
python scripts\test_dinov2.py
```

## 下一阶段

1. 完成产品、工位、配方 CRUD。
2. 增加标准图上传与 Canvas ROI 编辑器。
3. 建立视觉标准库并生成 DINOv2 Embedding。
4. 实现 ROI 裁剪、算法编排和规则判断。
5. 接入现有生产接口 `sn + image_paths`。

## V1.0 业务流程

```text
创建产品与工位
→ 创建配方
→ 上传标准整图
→ 在工作台框选 ROI
→ 为 ROI 添加相似度、颜色或 OCR 规则
→ 上传参考 ROI 图片并生成 Embedding
→ 上传生产图片测试配方
→ 发布配方
→ 第三方系统调用生产检测接口
```

配方工作台：`http://127.0.0.1:9010/workspace`

### 已支持的检测能力

| 能力 | 用途 | 配置示例 |
|---|---|---|
| `REFERENCE_SIMILARITY` | DINOv2 标准图相似度 | `{"min_similarity": 0.85}` |
| `COLOR_RATIO` | HSV 颜色像素比例 | `{"min_ratio": 0.15, "max_ratio": 1.0}` |
| `OCR_TEXT` | OCR 文本等于、包含、正则或前缀匹配 | `{"operator": "CONTAINS"}` |

### 配方导出

```http
GET /api/v1/configuration/recipes/{recipe_id}/export
```

该接口返回配方、标准图、ROI、检测项、算法能力、期望值和判定规则的完整 JSON 快照。

### 第三方生产检测

```http
POST /api/v1/inspection/execute
Content-Type: application/json
```

```json
{
  "sn": "SN202607280001",
  "recipe_code": "PDU001-ST01",
  "image_paths": [
    "/data/images/product_001.jpg"
  ]
}
```

兼容响应顶层字段：

```json
{
  "code": 0,
  "message": "success",
  "result": "OK",
  "image_paths": []
}
```

响应中同时包含配方版本、单图结果、每个 ROI 检测项结果、实际值、分数和耗时。
# 第三方检测接口

第三方系统通过以下接口提交产品条码和文件服务器图片路径：

```http
POST /api/detect
Content-Type: application/json
```

```json
{
  "sn": "PRODUCT-SN-001",
  "image_paths": [
    "C:\\vision-images\\image_01.jpg",
    "C:\\vision-images\\image_02.jpg"
  ]
}
```

响应格式：

```json
{
  "code": 0,
  "message": "success",
  "result": "OK",
  "image_paths": [
    "C:\\vision-platform\\detection_results\\result_1.jpg"
  ]
}
```

每次有效调用都会记录调用方 IP、调用时间、请求参数、响应参数、接口状态码和耗时，可在平台“检测记录”页面查看。
