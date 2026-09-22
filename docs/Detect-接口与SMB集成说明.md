# `/api/detect` 与 SMB 图片集成说明

## 1. 适用范围

本说明用于相机软件或外围系统调用平台的生产检测接口：

```text
POST http://<vision-platform-host>:9010/api/detect
```

接口会根据业务参数和图片文件名匹配**已发布**的工艺配方，下载图片到平台本地执行检测，并将标注结果图上传回原图片所在目录。

## 2. 调用参数

```json
{
  "line": "L40",
  "times": 1,
  "operation": "AS15",
  "materialCode": "CN0007998",
  "image_paths": [
    "\\\\caxaprdfile.catl.com\\prd-file\\MFG-ME-Report\\...\\AS15-CAMERA1PICTURE1-CN000798263700002-20260915134635901.jpg",
    "\\\\caxaprdfile.catl.com\\prd-file\\MFG-ME-Report\\...\\AS15-CAMERA2PICTURE1-CN000798263700002-20260915134635469.jpg"
  ]
}
```

| 字段 | 必填 | 说明 |
| --- | --- | --- |
| `line` | 是 | 拉线编码。 |
| `times` | 是 | 第几次拍照，正整数。 |
| `operation` | 是 | 工序编码。 |
| `materialCode` | 是 | 物料号；兼容 `materialcode` 和 `material_code`。 |
| `image_paths` | 是 | 最多两个图片路径；空字符串会自动跳过。 |
| `sn` | 否 | 产品条码。未传时，从图片名称解析。 |
| `camera` | 否 | 相机编号。正常由文件名解析；传入时必须与文件名一致。 |

图片文件名必须包含以下片段：

```text
CAMERA1PICTURE1-CN000798263700002-...
```

- `CAMERA1`：相机编号；
- `PICTURE1`：拍照次数；
- `CN000798263700002`：产品条码；
- 支持 `CAM01` 和 `CAMERA1` 两种配方相机编码视为同一相机；
- 两张图片都传入时，解析出的条码必须一致。

配方匹配键：

```text
line + materialCode + operation + camera + times + PUBLISHED
```

若图片为空、条码冲突、文件名与显式参数冲突或找不到已发布配方，接口直接返回 `ERROR`，不会使用猜测的配方继续检测。

## 3. 返回格式

```json
{
  "code": 200,
  "message": "success",
  "result": "OK",
  "image_paths": [
    "\\\\caxaprdfile.catl.com\\prd-file\\...\\AS15-CAMERA1PICTURE1-CN000798263700002-20260915134635901_result.jpg",
    "\\\\caxaprdfile.catl.com\\prd-file\\...\\AS15-CAMERA2PICTURE1-CN000798263700002-20260915134635469_result.jpg"
  ]
}
```

| 字段 | 含义 |
| --- | --- |
| `code=200` | 接口正常完成；可通过 `PUBLIC_DETECT_SUCCESS_CODE` 兼容旧系统。 |
| `message` | 接口处理消息。 |
| `result` | 产品检测结果：`OK`、`NG` 或 `ERROR`。 |
| `image_paths` | 与有效输入图片一一对应的标注结果图路径。结果文件名为原文件名加 `_result`。 |

内部还会保留 `inspection_results`，用于平台记录详情和故障排查；外围系统可以忽略该字段。

## 4. SMB 配置

在部署机器的项目根目录 `.env` 中配置，**密码只能放在 `.env`，不得提交 Git**：

```dotenv
SMB_ENABLED=true
SMB_SERVER_ROOT=\\caxaprdfile.catl.com\prd-file
SMB_USERNAME=<域账号>
SMB_PASSWORD=<密码>
SMB_CONNECTION_TIMEOUT_SECONDS=15
```

安装依赖后重启平台：

```text
smbprotocol==1.15.0
```

平台只允许访问 `SMB_SERVER_ROOT` 下的 UNC 路径，拒绝包含上级目录跳转的路径。若 SMB 未启用、账号缺失、登录失败或下载失败，`/api/detect` 返回 `code=1003`，不会假装检测成功。

调用方 IP 默认取 TCP 连接地址。只有平台部署在**可信反向代理**之后，才在服务器 `.env` 中配置 `TRUSTED_PROXY_HEADERS=true` 以读取 `X-Forwarded-For` / `X-Real-IP`；直连部署应保持默认 `false`，避免调用方伪造 IP。

## 5. 本地归档与结果上传

每次生产调用会在本地保留以下结构，便于检测记录详情追溯：

```text
detection_results/
  YYYYMMDD/
    <recipe_code>/
      detect-<request_id>/
        raw/                 原始下载图
        image_1/             ROI 图、标注图和中间处理结果
```

处理完成后，标注图通过 SMB 上传到原始图片同级目录，而不是上传到新的共享目录。

## 6. 并发与 VLM 实例池

- 两个相机图片按照配方并行执行；
- 同一图片中的 ROI 并行执行；
- `PRODUCTION_VLM_PARALLELISM=3` 全局限制 VLM 同时处理三张 ROI；
- 当服务器部署三个单图 VLM 实例时，在该 VLM 配置的“扩展参数 JSON”中填写：

```json
{
  "endpoint_pool": [
    "http://<vlm-host>:<port-1>/v1",
    "http://<vlm-host>:<port-2>/v1",
    "http://<vlm-host>:<port-3>/v1"
  ]
}
```

平台会轮询分配请求，并在“测试连接”时逐个显示三个端点的健康状态。不要把单图 VLM 配置为超过实际实例数量的并发，否则会排队、超时或显存不足。

## 7. 检测记录与报表

- 调用记录保存纠正后的产品条码、工序、真实调用方 IP、输入/输出参数及耗时；
- 每条记录的“详情”中展示配方标准图、实际原图、处理结果图，以及每个 ROI 的标准图和实际裁剪图；
- 统计报表按 ROI 关联的**场景名称**汇总调用次数、原始通过率、复核覆盖率、复核参考准确率和人工确认准确率；
- 复核参考准确率不是绝对质量真值。有人工作为最高优先级真值，其次为 VLM 复核；没有复核时仅展示原始检测结果。
