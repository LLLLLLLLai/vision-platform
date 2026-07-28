# 配置说明

运行配置统一放在项目根目录 `.env` 中。

算法服务地址通过以下变量管理：

```text
GROUNDING_SERVICE_URL
DINOV2_SERVICE_URL
PADDLEOCR_SERVICE_URL
```

生产环境建议关闭 `APP_DEBUG`，并使用反向代理限制管理端访问范围。

