# DINOv2 Service

DINOv2 Base 独立特征提取与相似度服务。模型启动时加载一次并常驻 GPU。

接口：

- `GET /health`
- `POST /v1/embedding`
- `POST /v1/similarity`

启动：

```powershell
python scripts\run_dinov2.py
```

平台通过 `.env` 中的 `DINOV2_SERVICE_URL` 调用。

