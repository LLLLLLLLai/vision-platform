# 随项目分发的 PaddleOCR 推理模型

| 目录 | 用途 | 官方来源 |
| --- | --- | --- |
| `PP-OCRv5_mobile_det/` | 定位图片中的文字区域 | [PaddlePaddle 模型页](https://huggingface.co/PaddlePaddle/PP-OCRv5_mobile_det) |
| `PP-OCRv5_mobile_rec_infer/` | 识别文字内容 | [PaddlePaddle 模型页](https://huggingface.co/PaddlePaddle/PP-OCRv5_mobile_rec)、[官方推理包](https://paddle-model-ecology.bj.bcebos.com/paddlex/official_inference_model/paddle3.0.0/PP-OCRv5_mobile_rec_infer.tar) |

两套模型由 PaddlePaddle/PaddleOCR 提供，官方模型页标注为 Apache-2.0；许可证原文见本目录 `LICENSE`。推理权重及推理配置按获取时内容保留，没有修改模型参数。

每个目录包含 `inference.json`、`inference.pdiparams`、`inference.yml`，其中识别配置包含字符字典，不能只复制权重文件。六个文件共约 21 MB，作为普通 Git 文件保存，无需 Git LFS；不要把缓存目录、虚拟环境或其他大型模型一起加入。

## 离线使用

在主项目安装 OCR 独立环境的依赖，运行 `start-ocr.ps1` 或 `bash start-ocr.sh`。保持 `OCR_DETECTION_MODEL_DIR`、`OCR_RECOGNITION_MODEL_DIR` 为空即可自动使用本目录；如需更换模型，可在 `.env` 中显式指定模型名称和匹配的绝对目录。

Python 依赖与 Paddle 运行库不包含在本目录，需要提前安装或制作离线依赖包。默认不启用文档旋转、文本方向或文档矫正附加模型。

## 完整性校验

在本目录运行 `sha256sum -c SHA256SUMS`。校验清单覆盖六个推理文件，模型文件使用 Git 属性关闭换行转换，Windows/Linux 拉取后校验值相同。更新模型时应同时更新校验清单并重新执行 `scripts/smoke_ocr_workflow.py` 验证。
