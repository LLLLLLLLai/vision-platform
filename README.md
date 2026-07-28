# Vision Platform

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
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
python scripts\init_db.py
python scripts\run.py
```

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

## 下一阶段

1. 完成产品、工位、配方 CRUD。
2. 增加标准图上传与 Canvas ROI 编辑器。
3. 建立视觉标准库并生成 DINOv2 Embedding。
4. 实现 ROI 裁剪、算法编排和规则判断。
5. 接入现有生产接口 `sn + image_paths`。

