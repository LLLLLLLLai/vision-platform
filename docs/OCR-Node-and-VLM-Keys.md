# OCR 流程节点与 VLM 密钥保存

## 流程使用

在流程节点库添加 **OCR 文字识别**，连接 `开始 → OCR → 结束`，或 `图片裁剪 → OCR → 规则判断 → 结束`。

- 输入 `image_path`：可选开始节点图片、配方裁剪后的 ROI，或前序图片裁剪节点输出。多图片需使用遍历处理节点，循环体内也支持 OCR。
- `min_score`：0～1，默认 0，不丢弃低置信度识别结果。按业务需要过滤，而不是把置信度当准确率。
- 输出 `text`：换行合并的文字；`texts`：文字行数组；`lines`：每行文字、置信度与四边形坐标；`text_count`：行数。
- 在后续规则实际值中选择 `{{ nodes.ocr_1.text }}`，或在 VLM 提示词中引用。结束输出可映射 `{"text":"{{ nodes.ocr_1.text }}"}`。
- OCR 只识别，不自带期望值，也不判定 OK/NG。未识别到文字返回空串和空列表、执行状态“已完成”；服务异常则执行失败，不能冒充未识别到文字。需要生产质量判断时使用规则或 VLM 节点；未产生质量结论时既有场景接口仍按 UNCERTAIN/保守 NG 规则处理，不改 detect 契约。
- 流程调试及检测记录包含 OCR 节点输入、输出、执行耗时。发布校验允许纯 OCR 场景。

## 独立 PaddleOCR 运行环境

OCR 依赖不加入主平台 requirements，避免 Paddle 与 YOLO/PyTorch 冲突。使用 PaddleOCR 3.x `predict`，默认使用仓库附带的 PP-OCRv5 mobile 检测及识别模型。接口与模型目录配置参考 [PaddleOCR 官方说明](https://www.paddleocr.ai/main/en/version3.x/pipeline_usage/OCR.html)。不恢复旧的 DINOv2/SAM/VLM 服务。

```bash
python -m venv ocr_service/.venv
ocr_service/.venv/bin/python -m pip install -r ocr_service/requirements.txt
bash start-ocr.sh
```

Windows 使用 `ocr_service\.venv\Scripts\python.exe -m pip install -r ocr_service\requirements.txt`，然后执行 `powershell -File start-ocr.ps1`。也可用 `OCR_PYTHON` 指定已有 OCR Python；Linux 支持复用激活的 conda 环境。GPU 部署需按服务器 CUDA 环境安装官方匹配的 Paddle GPU 包，不能同时安装 CPU/GPU Paddle 包。

`.env`：

```dotenv
OCR_SERVICE_BASE_URL=http://127.0.0.1:9024
OCR_SERVICE_TIMEOUT_SECONDS=120
OCR_SERVICE_API_KEY=
OCR_HOST=127.0.0.1
OCR_PORT=9024
OCR_DEVICE=cpu
OCR_DETECTION_MODEL_NAME=PP-OCRv5_mobile_det
OCR_RECOGNITION_MODEL_NAME=PP-OCRv5_mobile_rec
OCR_DETECTION_MODEL_DIR=
OCR_RECOGNITION_MODEL_DIR=
```

模型目录配置为空时，依次查找项目 `vision-models/paddleocr/<模型名称>`、`vision-models/paddleocr/<模型名称>_infer`、当前用户 `~/.paddlex/official_models/<模型名称>`。目录缺失时明确报错，不在生产请求中自动下载。目录需含完整推理配置和权重，例如 `inference.yml`、`inference.json`、`inference.pdiparams`。

本次已将两套默认 OCR 推理权重提交到 Git：`PP-OCRv5_mobile_det/` 与 `PP-OCRv5_mobile_rec_infer/`。服务器拉取代码后无需再次下载这两套模型；如 `.env` 原来指定了失效的旧机器模型路径，应清空两个目录配置或改为服务器上的绝对路径。许可证、来源和 SHA-256 校验见 [模型说明](../vision-models/paddleocr/README.md)。离线环境仍需提前准备 Python/Paddle 依赖，本仓库不包含虚拟环境或依赖安装包；其他 YOLO/VLM 权重不在此次上传范围。

服务默认关闭文档旋转、文档矫正、文字行方向模型，避免额外下载；严重旋转或模糊文字并不保证识别准确，应先用真实 ROI 测试。更换 PP-OCRv6 等模型时同时配置匹配的名称、目录及 Paddle 版本。

服务 `GET /health` 返回进程状态和 `model_loaded`，尚未加载模型不等于识别已可用；首个 `/ocr` 请求加载模型。平台以 multipart 的 `image` 字段上传图片内容，跨服务器不依赖同名本地路径。默认仅监听回环；远程部署必须配置双方相同的 `OCR_SERVICE_API_KEY`、网络访问限制及 TLS 代理。请求头为 `X-OCR-API-Key`。单图片上限 20 MB、3200 万像素；单推理实例串行使用模型，最多接收两项并发工作，额外请求 429，不无限积压。

主平台日志记录请求失败原因；OCR 启动终端记录模型错误详情。缺依赖、目录错误或模型推理失败返回 503；超时不会返回伪造文字。

## VLM API Key 自动加密保存

1. VLM 配置中直接填写 API Key 并保存。无需用户先配置 `VLM_SECRET_KEY`。
2. 平台首次加密保存时，在 `data/secrets/vlm.key` 自动生成随机 Fernet 主密钥。数据库仅保存密文，列表/详情不返回明文；编辑输入框留空保留旧 Key。
3. `.env` 中已有 `VLM_SECRET_KEY` 时继续优先使用，绝不替换。也可用 `VLM_SECRET_KEY_FILE` 指定密钥文件路径。
4. 主密钥已加入 Git 忽略规则。Linux 新文件权限 0600，新目录 0700；Windows 继承目录 ACL，应仅授权服务账号和管理员。
5. **迁移或备份时必须同时保存数据库与原主密钥，分开安全保管。** 多个应用实例连接同一数据库必须使用同一主密钥或共享持久化密钥文件；不能在不同容器分别生成密钥。
6. 数据库存在旧密文但文件丢失时禁止自动生成新主密钥，需恢复旧文件/环境变量。密钥轮换不是直接修改值，需另行迁移现有密文。
7. 高级部署仍可选 API Key 环境变量名称；与直接输入二选一。切换为直接输入时清除旧环境变量引用；切回环境变量时删除旧密文。无需数据库表结构变更。

回归测试使用独立 SQLite 和临时密钥目录，不读写生产密钥或业务数据；包括并发首次生成、旧配置保留、错误主密钥、缺失密钥保护、OCR 映射、发布、循环及异常处理。

OCR 服务启动后，在主平台 Python 环境执行 `python scripts/smoke_ocr_workflow.py`，或追加服务地址 `python scripts/smoke_ocr_workflow.py http://127.0.0.1:9024`。脚本创建临时数据库和合成文字图片，通过已发布场景接口执行“开始 → OCR → 规则 → 结束”，验证返回文字包含 `FU5`。这只是连通性回归，不代表真实生产图片的 OCR 准确率。
