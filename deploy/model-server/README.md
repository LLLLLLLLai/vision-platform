# 160 服务器：YOLO 推理 Docker 服务

本目录只部署训练完成的 YOLO 推理服务。Qwen/VLM 已运行在 162 服务器，因此不在 160 重复启动 VLM 容器。

## 前置条件

1. 160 已安装 Docker Engine、Docker Compose 插件和 NVIDIA Container Toolkit。
2. 运行 `nvidia-smi` 能看到 GPU。
3. 160 上准备模型目录，例如 `/srv/vision-models`；它的文件层级必须和应用服务器 `VISION_MODEL_STORAGE_ROOT` 一致。
4. 把 `.env.example` 复制为 `.env`，填写模型目录和随机 API Key。

例如应用服务器保存的权重为：

```text
vision-models/imported/ai_harness/v1/best.pt
```

则 160 的 `/srv/vision-models` 必须包含：

```text
/srv/vision-models/imported/ai_harness/v1/best.pt
```

可通过只读 NAS/NFS 挂载实现，也可由发布流程同步文件；不要让两个服务器各自维护不同目录结构。

## 启动

在项目根目录执行：

```bash
cp deploy/model-server/.env.example deploy/model-server/.env
docker compose --env-file deploy/model-server/.env -f deploy/model-server/compose.yaml up -d --build
docker compose --env-file deploy/model-server/.env -f deploy/model-server/compose.yaml ps
curl http://127.0.0.1:8091/health
```

应用服务器需能访问 `http://<160服务器IP>:8091`；防火墙只向应用服务器开放 TCP `8091`。

## 运行职责

- `POST /v1/yolo/infer`：接收一张临时上传图片和已发布模型的相对权重键，返回统一的检测、分割或分类结果。
- `GET /health`：检查容器、GPU 与模型目录挂载状态。
- 容器不访问业务数据库、不访问 SMB，也不保存生产图片。
