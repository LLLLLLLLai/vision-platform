# DINOv2 Service

保留给现有 DINOv2 独立服务。

平台约定接口：

- `GET /health`
- `POST /v1/embedding`
- `POST /v1/similarity`

请保留已有模型加载和推理代码；平台通过 `.env` 中的 `DINOV2_SERVICE_URL` 调用。

