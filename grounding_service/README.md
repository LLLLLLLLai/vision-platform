# Grounding DINO Service

负责把 Qwen3-VL 生成的物体类型和英文视觉短语定位成候选框。

- `GET /health`
- `POST /v1/localize`

启动：

```powershell
.\.venv-qwen\Scripts\python.exe -m uvicorn grounding_service.main:app --host 127.0.0.1 --port 9021
```
