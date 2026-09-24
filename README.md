# Vision Platform

面向汽车电子装配错装、漏装、混装检测的工业视觉智能平台。

## 当前文档

- 当前交接手册：`docs/Vision-Platform-Handover-V3.0.md`
- 场景、数据集与模型架构：`docs/Scene-Dataset-Model-Architecture.md`
- MySQL 8 迁移说明：`docs/MySQL-Migration.md`
- MySQL 表结构：`docs/MySQL-Table-Structure.md`
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

- FastAPI、SQLAlchemy 2.0；本地可使用 SQLite，生产目标为 MySQL 8。
- 工艺配方版本管理、ROI/特征点编辑与对齐、发布和回滚。
- 场景管理：VLM 直接检测与可视化流程编排；每个发布场景均可生成独立 API。
- 数据集管理：测试集 OK/NG 真值、YOLO 检测/分割/分类在线标注及离线 YOLO ZIP 导入。
- 模型中心：OpenAI 兼容 VLM 配置、YOLO 模型定义、训练任务、训练指标与模型版本发布。
- 生产检测：两相机图片和同图 ROI 受控并发执行，SMB 下载原图、回写结果图。
- 运营能力：检测记录、场景评测、提示词优化、VLM 异步复核和场景维度报表。
- 图像对齐：固定工位下支持特征点补偿小范围平移和轻微旋转。

## 本地模型服务已下线

以下旧版独立服务及其启动、下载脚本已删除：

```text
DINOv2
SAM2
PaddleOCR
本地 Qwen3-VL
```

当前平台仅通过“VLM 模型配置”调用用户维护的 **OpenAI 兼容 VLM 接口**，并通过流程场景调用已发布的 YOLO 权重。

- `GET /api/v1/algorithms/status` 仅返回旧服务已下线说明。
- `POST /api/v1/algorithms/vlm/judge` 返回 `410 Gone`，请改用已发布 VLM 场景接口。
- 生产配方中的每个启用 ROI 必须关联已发布检测场景；未关联时 `/api/detect` 返回明确的 `ERROR`，不会回退调用旧模型或误判为 `OK`。
- `vision-models/` 中的权重不会因本次变更删除，可继续作为 YOLO 基础权重或已发布权重使用。

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
vision-models/            YOLO 基础/发布权重，不纳入 Git
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
python3.11 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
cp .env.example .env
./start.sh
```

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

## 新增或修改检测能力

1. 在“VLM 模型配置”维护 OpenAI 兼容地址、模型名和密钥，测试连接。
2. 创建 VLM 场景，或创建流程场景并组合已发布 YOLO、VLM、裁剪、规则和 Web API 节点。
3. 使用测试数据集评测场景；确认后发布场景版本。
4. 在工艺配方 ROI 中绑定该已发布场景并填写场景字段值。
5. 保存草稿配方并进行测试，确认后发布配方。

只有已发布的场景与配方会进入生产检测链路。
