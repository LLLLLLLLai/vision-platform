# Grounding DINO Service

保留给现有 Grounding DINO 独立服务。

平台约定接口：

- `GET /health`
- `POST /v1/localize`

请保留已有模型加载和推理代码；平台通过 `.env` 中的 `GROUNDING_SERVICE_URL` 调用。

