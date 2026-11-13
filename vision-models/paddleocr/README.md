# 随项目分发的 PaddleOCR 推理模型

| 目录 | 用途 | 官方来源 |
| --- | --- | --- |
| `PP-OCRv5_mobile_det/` | 定位图片中的文字区域 | [PaddlePaddle 模型页](https://huggingface.co/PaddlePaddle/PP-OCRv5_mobile_det) |
| `PP-OCRv5_mobile_rec_infer/` | 识别文字内容 | [PaddlePaddle 模型页](https://huggingface.co/PaddlePaddle/PP-OCRv5_mobile_rec)、[官方推理包](https://paddle-model-ecology.bj.bcebos.com/paddlex/official_inference_model/paddle3.0.0/PP-OCRv5_mobile_rec_infer.tar) |
| `PP-LCNet_x1_0_doc_ori_infer/` | 整图 0/90/180/270 度方向分类 | [PaddlePaddle 模型页](https://huggingface.co/PaddlePaddle/PP-LCNet_x1_0_doc_ori) |
| `PP-LCNet_x1_0_textline_ori_infer/` | 文字行 0/180 度方向分类 | [PaddlePaddle 模型页](https://huggingface.co/PaddlePaddle/PP-LCNet_x1_0_textline_ori) |
| `UVDoc_infer/` | 文档图像去畸变 | [PaddlePaddle 模型页](https://huggingface.co/PaddlePaddle/UVDoc) |

五套模型由 PaddlePaddle/PaddleOCR 提供，官方模型页标注为 Apache-2.0；许可证原文见本目录 `LICENSE`。推理权重及推理配置按获取时内容保留，没有修改模型参数。

每个目录包含 `inference.json`、`inference.pdiparams`、`inference.yml`，其中识别配置包含字符字典，不能只复制权重文件。十五个推理文件共 67,702,403 字节（约 67.7 MB），作为普通 Git 文件保存，无需 Git LFS；不要把下载缓存、示例图片、虚拟环境或其他大型模型一起加入。

## 离线使用

在主项目安装 OCR 独立环境的依赖，运行 `start-ocr.ps1` 或 `bash start-ocr.sh`。保持 `OCR_DETECTION_MODEL_DIR`、`OCR_RECOGNITION_MODEL_DIR` 为空即可自动使用本目录；如需更换模型，可在 `.env` 中显式指定模型名称和匹配的绝对目录。

Python 依赖与 Paddle 运行库不包含在本目录，需要提前安装或制作离线依赖包。默认不启用文档旋转、文本方向或文档矫正附加模型。

方向分类、去畸变所需的三套模型也已随 Git 分发，服务器拉取后无需运行下载脚本。在流程的 OCR 节点手动启用对应开关即可：方向分类同时使用两个 PP-LCNet 模型，去畸变使用 UVDoc。保持 `OCR_DOC_ORIENTATION_MODEL_DIR`、`OCR_TEXTLINE_ORIENTATION_MODEL_DIR`、`OCR_UNWARPING_MODEL_DIR` 为空时自动查找本目录；若旧 `.env` 写死了另一台机器的路径，需要清空或改为当前服务器路径。升级后重启 OCR 服务与主平台。

方向分类不能保证校正任意角度、弯曲线束；UVDoc 主要针对文档形变。启用后应比较真实素材效果与耗时，预处理后的坐标不能直接用于原图叠框。仅在权重缺失且机器联网时才需要 `python scripts/download_ocr_preprocessing.py` 作为补下载工具。

## 完整性校验

在本目录运行 `sha256sum -c SHA256SUMS`。校验清单覆盖十五个推理文件，模型文件使用 Git 属性关闭换行转换，Windows/Linux 拉取后校验值相同。更新模型时应同时更新校验清单；启动 OCR 服务后，在项目根目录运行 `python scripts/smoke_ocr_preprocessing.py http://127.0.0.1:9024` 验证两种开关的组合，也可运行 `scripts/smoke_ocr_workflow.py` 验证场景调用链。
