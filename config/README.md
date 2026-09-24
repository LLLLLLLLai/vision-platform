# 配置说明

运行配置统一放在项目根目录 `.env` 中；请从 `.env.example` 复制创建，真实密钥不得提交 Git。

当前模型接入方式：

- **VLM**：在“模型中心 → VLM 模型配置”维护 OpenAI 兼容 `base_url`、模型名、密钥、思考模式与扩展参数。
- **YOLO**：在“模型中心 → 训练模型维护 / 模型训练”创建、训练并发布模型版本，由流程场景的“训练模型”节点调用。
- **本地规则**：`config/harness.json` 仅保留 OpenCV 颜色、图像质量和特征点对齐能力。

本地 DINOv2、SAM2、PaddleOCR、Qwen3-VL 服务配置已退役，不再需要任何服务地址变量。

生产环境建议关闭 `APP_DEBUG`，配置 MySQL、SMB、`SCENE_API_KEY`，并通过反向代理限制管理端访问范围。
