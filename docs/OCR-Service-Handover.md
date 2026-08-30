# OCR 服务交接与服务器迁移

## 1. 服务职责

`ocr_service` 是视觉平台的专用文字识别服务，默认监听 `9024` 端口。平台通过 `POST /ocr` 传入共享目录中的 ROI 图片路径，OCR 服务返回文字、行级置信度和预处理诊断信息。

它只负责“读到什么文字”，最终的 `OK / NG` 由主平台按配方中的文字规则决定。

## 2. 本次更新

- OCR 模型不再固定为 PP-OCRv5；由环境变量选择 V5 或 V6 的检测、识别模型。
- `scripts/run_ocr.py` 启动时自动读取项目根目录 `.env`，云服务器配置不再依赖手工导出环境变量。
- OCR 会过滤低置信度的杂散字符，避免单个噪声字符拉低有效文字的置信度。
- 当结果为空、低置信度或不符合配置的期望文字时，服务自动尝试旋转、放大、对比度增强和锐化后的候选图。
- 期望文字只用于候选结果排序和规则匹配，**不会**把识别结果中的 `S` 强制改成 `5`。

## 3. 需要迁移的文件

不要只复制单个 `ocr_service.py`。应迁移以下文件：

```text
ocr_service/
  __init__.py
  engine.py
  main.py
  schemas.py
  requirements.txt
scripts/run_ocr.py
start-ocr.sh
.env
```

云服务器必须重新创建 Linux Python 虚拟环境，不能复制 Windows 的 `.venv`。

## 4. Red Hat Enterprise Linux 8.3 部署

以下示例使用 Python 3.11。PaddlePaddle 的 CPU/GPU 安装包必须与服务器的 Python、CUDA 和驱动版本匹配；请按 PaddlePaddle 官方安装说明选择对应包。

```bash
cd /opt/vision-platform
python3.11 -m venv ocr_service/.venv
ocr_service/.venv/bin/python -m pip install --upgrade pip
ocr_service/.venv/bin/python -m pip install -r ocr_service/requirements.txt
chmod +x start-ocr.sh
```

首次启动会自动下载配置中指定的 PaddleOCR 模型。无网络服务器应在有网络环境预下载模型后，复制模型缓存或使用内部制品库；不要把模型文件提交到 Git。

## 5. 模型配置

在项目根目录 `.env` 中配置。服务启动时自动读取该文件。

### 本地 PP-OCRv5 Mobile

```dotenv
PADDLEOCR_SERVICE_URL=http://127.0.0.1:9024
OCR_HOST=0.0.0.0
OCR_PORT=9024
OCR_DEVICE=cpu
OCR_CPU_THREADS=6
OCR_MIN_LINE_CONFIDENCE=0.40
OCR_USE_TEXTLINE_ORIENTATION=false
OCR_ENABLE_MKLDNN=true
OCR_VERSION=PP-OCRv5
OCR_DETECTION_MODEL=PP-OCRv5_mobile_det
OCR_RECOGNITION_MODEL=PP-OCRv5_mobile_rec
```

### 云服务器 PP-OCRv6 Medium

将下面三项替换到云服务器 `.env`：

```dotenv
OCR_VERSION=PP-OCRv6
OCR_DETECTION_MODEL=PP-OCRv6_medium_det
OCR_RECOGNITION_MODEL=PP-OCRv6_medium_rec
```

其余预处理逻辑和 API 不变。上线前必须使用现场 ROI 图片进行 A/B 验证，确认 V6 对目标线束、标签和字符集的准确率后再替换生产服务。

## 6. 启动与停止

前台启动：

```bash
cd /opt/vision-platform
./start-ocr.sh
```

健康检查：

```bash
curl http://127.0.0.1:9024/health
```

预期返回示例：

```json
{
  "status": "READY",
  "model": "PP-OCRv6_medium_rec",
  "device": "cpu",
  "error": null
}
```

生产环境建议由 systemd 管理。`ExecStart` 使用：

```text
/opt/vision-platform/start-ocr.sh
```

并在 unit 中配置：

```text
WorkingDirectory=/opt/vision-platform
Restart=always
RestartSec=5
```

## 7. API

### `GET /health`

检查模型是否加载完成。

### `POST /ocr`

请求：

```json
{
  "image_path": "/data/images/20260830/ROI_6.jpg",
  "expected_text": "FU7"
}
```

响应重点字段：

```json
{
  "code": 0,
  "model": "PP-OCRv6_medium_rec",
  "text": "FU7",
  "confidence": 0.96,
  "lines": [],
  "raw_text": "FU7 ?",
  "filtered_noise_count": 1,
  "preprocessing": {
    "fallback_used": false,
    "selected_variant": "ORIGINAL",
    "expected_matched": true
  }
}
```

`text` 是过滤低置信度噪声后的有效文字；`raw_text` 用于排障。生产规则应使用完整编码，例如 `FU7` 或 `FU5`，不要只配置 `FU` 这种短前缀。

## 8. 图片目录要求

当前 API 传输的是 `image_path`，不会把图片二进制上传给 OCR 服务。因此主平台和 OCR 服务必须访问同一份图片。

推荐统一挂载：

```text
主平台：/data/images
OCR 服务：/data/images
```

请求中只能传 OCR 服务所在主机可读取的绝对路径。若两者在不同服务器，使用 NFS、SMB 或对象存储挂载，并保持路径一致。

## 9. 已知限制与处置

- 对曲面、小字号、反光或扭曲线束字符，通用 OCR 可能将 `5` 识别为 `S`。模型置信度不能单独作为正确性依据。
- PP-OCRv6 应作为优先测试方案，但不应未验证即宣称解决所有工业字符问题。
- OCR 与完整期望编码不一致时，应按现场质量策略判 `NG` 或转人工复核，禁止把字符直接强制替换。
- 真正锁付质量应以电批扭矩/角度或 PLC 完成信号为准；OCR 和视觉只验证外观、线号和漏装。

## 10. 上线检查

1. `GET /health` 返回 `READY`，模型名为预期 V6 模型。
2. 用 20 张以上现场 ROI 对 V5 与 V6 做对比，记录正确、误判、漏判和耗时。
3. 确认主平台 `PADDLEOCR_SERVICE_URL` 指向云服务器地址。
4. 确认主平台与 OCR 服务均可读取相同的 `/data/images` 路径。
5. 使用一条真实配方调用 `/api/detect`，检查检测记录中保存了 OCR 原始结果和最终规则结果。
