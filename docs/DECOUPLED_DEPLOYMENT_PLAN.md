# 视觉检测平台解耦部署方案

## 1. 目标与边界

本方案将平台拆为生产检测面与管理控制面，避免训练、场景优化、数据集管理等非实时功能影响 `detect` 和已发布场景的生产调用。

本次交付已完成两项基础能力：

1. **160 GPU 服务器**：提供 Docker 化的统一 YOLO 推理服务，支持目标检测、分割和分类三种已发布 YOLO 模型。
2. **应用服务器**：同一代码基于 `APP_ROLE` 运行成两个独立服务，并可将 YOLO 调用切换到 160 服务。

162 服务器上现有的 vLLM/Qwen 服务继续使用，不在 160 重复部署。

## 2. 目标部署图

```text
                         ┌───────────────────────────┐
                         │      管理人员 / 浏览器      │
                         └─────────────┬─────────────┘
                                       │ 9010
                        ┌──────────────▼──────────────┐
                        │ 应用服务器：Control Service  │
                        │ 配方、场景、数据集、模型管理 │
                        │ 评测/优化/训练异步任务       │
                        └──────────────┬──────────────┘
                                       │ MySQL
  相机/外围系统 ────────► 9011          │
┌──────────────────────┐              │
│ Inspection Service   │──────────────┼───────────┐
│ /api/detect          │              │           │
│ 已发布场景 API       │              │           │
└──────┬───────┬───────┘              │           │
       │       │                      │           │
       │       ├── SMB/NAS 图片读写   │           │
       │       │                      │           │
       │       ├── HTTP ──────────────▼───────────┤
       │       │              160 GPU Server       │
       │       │          Docker: YOLO Inference    │
       │       │              TCP 8091              │
       │       │                                    │
       │       └── HTTP ───────────────────────────▼
       │                      162 GPU Server
       │                  vLLM / Qwen OpenAI API
       │                      TCP 8000 (example)
       │
       └── shared model storage (NAS/NFS, read-only at runtime)
```

## 3. 服务职责

| 服务 | 端口 | 职责 | 不应承担的职责 |
|---|---:|---|---|
| `vision-control` | 9010 | Web 页面、工艺配方、场景、数据集、模型登记、报表、训练/评测/优化/复核异步任务 | 对外实时 `detect` |
| `vision-inspection` | 9011 | `/api/detect`、已发布场景 API、SMB 图片读写、配方匹配、ROI/特征点对齐、结果图回传 | 模型训练、提示词优化、批量评测 |
| `vision-yolo-inference` | 8091 | 在 160 的 GPU 上加载和缓存 YOLO 权重，执行检测/分割/分类 | MySQL、SMB、工艺配方逻辑 |
| 162 vLLM | 现有端口 | OpenAI 兼容的 VLM 场景与节点调用 | YOLO 模型推理 |

已发布的场景接口也属于生产检测面：

```text
POST /api/v1/scenarios/invoke/{scene_code}
```

它随 `vision-inspection` 服务发布，不依赖管理页面服务。

## 4. 160 服务器 Docker 部署

部署包位于：`deploy/model-server/`。

### 4.1 模型目录约束

160 的模型目录必须与应用服务器 `VISION_MODEL_STORAGE_ROOT` 的相对层级一致。例如：

```text
应用服务器：/mnt/vision-models/imported/ai_harness/v1/best.pt
160 服务器：/srv/vision-models/imported/ai_harness/v1/best.pt
```

平台只把相对路径 `imported/ai_harness/v1/best.pt` 传给 160，模型服务器不会接受绝对路径或含 `..` 的路径。

推荐以 NAS/NFS 只读挂载同步权重；如暂时不能共享挂载，则在“模型版本发布”前将文件同步到 160 的相同相对位置。

### 4.2 启动步骤

在 160 服务器的项目根目录执行：

```bash
cp deploy/model-server/.env.example deploy/model-server/.env
vi deploy/model-server/.env
docker compose --env-file deploy/model-server/.env -f deploy/model-server/compose.yaml up -d --build
curl http://127.0.0.1:8091/health
```

健康响应中应至少确认：

- `status` 为 `READY`；
- `cuda_available` 为 `true`；
- `model_storage_ready` 为 `true`。

仅允许应用服务器访问 TCP `8091`，不要将此端口暴露给公网或产线以外的网段。

## 5. 应用服务器双服务部署

示例环境文件和 systemd 单元位于 `deploy/application-server/`：

- `control.env.example`、`vision-control.service`
- `inspection.env.example`、`vision-inspection.service`

将示例复制到 `/etc/vision-platform/` 后填写 MySQL、SMB、密钥和服务器地址。两个服务共享 MySQL 与模型目录，但运行目录、日志和端口应分开。

```bash
sudo cp deploy/application-server/vision-control.service /etc/systemd/system/
sudo cp deploy/application-server/vision-inspection.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now vision-control vision-inspection
sudo systemctl status vision-control vision-inspection
```

本地开发保持：

```text
APP_ROLE=ALL
```

即可继续按原方式启动，不改变现有页面和接口。

## 6. 应用到模型服务连接配置

应用服务器的检测服务环境变量：

```dotenv
APP_ROLE=INSPECTION
YOLO_INFERENCE_MODE=REMOTE
YOLO_INFERENCE_BASE_URL=http://<160服务器IP>:8091
YOLO_INFERENCE_API_KEY=<与160的YOLO_INFERENCE_API_KEY一致>
VISION_MODEL_STORAGE_ROOT=/mnt/vision-models
```

VLM 不通过新增的 YOLO 服务转发。在管理页“VLM 模型配置”中，继续将 Base URL 配置为：

```text
http://<162服务器IP>:8000/v1
```

并填写 162 的模型 ID、API Key（如 vLLM 启动时启用了认证）和超时参数。

## 7. 生产调用链

### 7.1 `detect` 接口

1. 读取现有固定参数与图片路径；空图片路径直接跳过。
2. 并行经 SMB 暂存有效图片。
3. 解析相机编号、条码，匹配已发布工艺配方。
4. 使用配方特征点校正 ROI；同一图片的 ROI 并行执行。
5. VLM 场景调用 162；YOLO 节点上传 ROI 到 160。
6. 汇总结果，写检测记录、节点耗时、结果图，并通过 SMB 回传 `_result` 图片。

`/api/detect` 的请求和返回字段保持不变。

### 7.2 已发布场景接口

外部系统调用：

```text
POST /api/v1/scenarios/invoke/{scene_code}
```

该接口只执行场景当前已发布版本；草稿版本不会被外部调用到。

## 8. 上线验收清单

1. 160：`/health` 显示 GPU 和模型目录正常。
2. 应用服务器：`GET http://<inspection>:9011/api/v1/health` 正常。
3. 使用一个已发布 YOLO 流程场景测试，检测记录的节点追踪中能看到 `runtime=REMOTE`。
4. 使用一个 VLM 场景测试，确认请求到达 162 vLLM。
5. 调用原有 `/api/detect`，确认 SMB 下载、两图并行、ROI 结果图回传以及检测记录均正常。
6. 停止 `vision-control` 后，9011 的 `detect` 和已发布场景 API 仍可运行；这是解耦的关键验收。

## 9. 风险与后续建议

- **模型文件一致性**：模型发布流程应增加“同步到 160 并健康检查”的步骤，否则应用能看到已发布版本但 160 无对应权重。
- **GPU 抢占**：训练必须留在 Control/训练资源，不要与 160 实时 YOLO 容器争用同一张 GPU。
- **VLM 限流**：162 的 vLLM 实例数量决定 `PRODUCTION_VLM_PARALLELISM`，当前应按其真实并发能力设置。
- **高可用**：首期使用单个 160 服务即可；后续可在 `YOLO_INFERENCE_BASE_URL` 前增加内部负载均衡，或扩展为多个模型服务器。
- **密钥管理**：所有 API Key、SMB 密码、数据库密码仅放服务器环境文件，禁止写入 Git、文档截图或数据库明文。
