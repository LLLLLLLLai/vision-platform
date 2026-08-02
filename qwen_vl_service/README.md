# Qwen3-VL 本地复核服务

该服务将 `Qwen/Qwen3-VL-4B-Instruct` 封装为工业视觉复核接口，默认监听 `9023` 端口。

本机 RTX 4060 Ti 只有 8GB 显存，默认使用 4-bit NF4 量化并限制 GPU 使用量。生产环境的 L20 48GB 可以把 `QWEN_VL_QUANTIZATION` 改为 `none`，使用 FP16。

## 安装与下载

```powershell
.\.venv\Scripts\python.exe -m venv .venv-qwen
.\.venv-qwen\Scripts\python.exe -m pip install torch torchvision --index-url https://download.pytorch.org/whl/cu121
.\.venv-qwen\Scripts\python.exe -m pip install -r qwen_vl_service\requirements.txt
.\.venv-qwen\Scripts\python.exe scripts\download_qwen_vl.py
```

下载脚本默认使用 ModelScope 国内源，也可传入 `--source huggingface`。

模型默认保存到 `vision-models/qwen3-vl-4b-instruct`。

## 启动与测试

```powershell
.\.venv-qwen\Scripts\python.exe scripts\run_qwen_vl.py
.\.venv-qwen\Scripts\python.exe scripts\test_qwen_vl.py --image test_images\test.jpeg
```

接口：

- `GET /health`
- `POST /v1/load`
- `POST /v1/judge`

生产检测只应传 ROI 小图。VLM 用于复杂安装关系或低置信度复核，不作为每个检测点的主判断模型。
