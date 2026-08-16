# Vision Platform

- 系统交接文档：`docs/Vision-Platform-System-Handover.md`
- Word 交接文档：`docs/Vision-Platform-System-Handover.docx`
- 启动与接口 SOP：`docs/Vision-Platform-Operation-SOP.docx`

面向汽车电子装配错装、漏装、混装检测的工业视觉智能平台。第一阶段采用“工业配方 + 产品世界模型 + 可替换感知服务 + 规则决策”的结构。

## 架构

```text
相机软件 / 文件服务器
        |
        v
生产检测接口
        |
        v
配方路由（拉线 + 物料 + 工序 + 相机 + 拍照次数）
        |
        v
产品世界模型（对象、空间位置、期望状态、对象关系）
        |
        v
算法能力（DINOv2 / Grounding DINO / SAM2 / OCR / OpenCV / Qwen3-VL）
        |
        v
状态理解与规则引擎
        |
        v
OK / NG / ERROR
```

模型通过独立 HTTP 服务接入，平台只依赖能力接口，不绑定具体模型。

## 已实现

- FastAPI 后端、SQLite 和 SQLAlchemy 2.0。
- 产品、工位、配方、ROI、检测项、视觉标准库和检测记录。
- 显式配方业务键：`line_code + material_code + process_code + camera_code + capture_index`。
- 配方按结构化参数优先匹配，缺省时兼容从图片文件名解析。
- 产品世界模型：ROI 自动映射为场景对象，并按相机和拍照次数保存多视角位置。
- 产品世界模型与 ROI 对象可视化编辑页面。
- 上传配方图片后由 Qwen3-VL 自动生成候选物体框，用户确认、移动、缩放或删除后再写入正式 ROI。
- DINOv2、Grounding DINO、SAM2、PaddleOCR、Qwen3-VL 服务接口预留。
- 线束采用 Grounding DINO 粗定位、SAM2 像素级分割和橙色 HSV 快速分割融合。
- Qwen3-VL 4B 本地 4-bit 测试服务。
- DINOv2 低置信度区间自动触发 Qwen3-VL 复核，异常时按安全策略判定。

## 目录

```text
app/
  api/                    API 路由
  core/                   配置
  db/                     数据库会话、初始化和轻量升级
  models/                 配方、检测、标准库和世界模型 ORM
  services/               检测执行与算法客户端
  static/                 管理页面静态资源
  templates/              管理页面模板
config/                   配置说明
scripts/                  初始化、启动、下载和测试脚本
tests/                    单元测试
dinov2_service/           DINOv2 独立服务
grounding_service/        Grounding DINO 独立服务
qwen_vl_service/          Qwen3-VL 独立服务
sam2_service/             SAM2.1 线束分割独立服务
vision-models/            本地模型文件，不纳入 Git
test_images/              测试图片
```

## 平台启动

Windows：

```powershell
cd C:\Users\Administrator\Desktop\vision-platform
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
.\start.ps1
```

Linux：

```bash
python3.11 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
cp .env.example .env
./start.sh
```

访问：

- 管理页面：`http://127.0.0.1:9010`
- API 文档：`http://127.0.0.1:9010/docs`
- 算法状态：`http://127.0.0.1:9010/api/v1/algorithms/status`

## 配方规则

推荐配方编码：

```text
{LINE}_{MATERIAL}_{PROCESS}_{CAMERA}_P{CAPTURE_INDEX}
```

例如：

```text
L01_PDU001_AS10_CAM01_P01
```

结构化调用优先：

```json
{
  "sn": "SN202608010001",
  "line_code": "L01",
  "material_code": "PDU001",
  "process_code": "AS10",
  "camera_code": "CAM01",
  "capture_index": 1,
  "image_paths": ["D:/vision-images/product_001.jpg"]
}
```

兼容调用：

```json
{
  "sn": "SN202608010001",
  "image_paths": [
    "D:/vision-images/L01_PDU001_AS10_CAM01_P01_001.jpg"
  ]
}
```

匹配优先级为：结构化参数、图片名称。生产接口为 `POST /api/detect`，顶层返回继续兼容 `code`、`message`、`result` 和 `image_paths`。

## 产品世界模型与复核策略

每个配方 ROI 会同步为产品世界模型中的场景对象。一个对象可在不同相机和拍照次数下拥有独立视角坐标，视角键格式为 `CAMERA:Pnn`。对象保留类型、定位方式、期望状态和感知能力，ROI 仍作为生产检测的确定性执行区域。

DINOv2 相似度结果仅在配置的边界区间内触发 Qwen3-VL：

```text
明确通过或明确失败 -> 直接使用 DINOv2 结果
处于低置信度边界 -> 裁剪 ROI 后调用 Qwen3-VL 复核
VLM 返回 UNCERTAIN、格式错误或服务不可用 -> 按 NG 安全降级
```

复核开关、上下限和提示词均在 ROI 对象编辑页面中配置。Qwen3-VL 默认注册为 `FALLBACK_ONLY`，不会成为每个检测点的必经链路。

配方配置阶段采用“自动发现优先、人工确认兜底”的方式：

```text
上传产品图片
-> Qwen3-VL 解析可检测物体和候选框
-> 页面显示紫色虚线候选框
-> 用户移动、缩放、删除或确认
-> 确认后的对象写入产品世界模型并生成正式 ROI
-> 自动发现服务不可用时仍可手动画框
```

## Qwen3-VL 本地测试

当前桌面机器检测到 RTX 4060 Ti 8GB，建议使用独立环境和 4-bit 量化：

```powershell
python -m venv .venv-qwen
.\.venv-qwen\Scripts\python.exe -m pip install torch torchvision --index-url https://download.pytorch.org/whl/cu121
.\.venv-qwen\Scripts\python.exe -m pip install -r qwen_vl_service\requirements.txt
.\.venv-qwen\Scripts\python.exe scripts\download_qwen_vl.py
.\.venv-qwen\Scripts\python.exe scripts\run_qwen_vl.py
```

默认使用 ModelScope 国内源下载；如需 Hugging Face，可增加参数
`--source huggingface`。

另开终端测试：

```powershell
.\.venv\Scripts\python.exe scripts\test_qwen_vl.py --image test_images\test.jpeg
```

模型服务端口为 `9023`。平台代理接口为 `POST /api/v1/algorithms/vlm/judge`。只应向 VLM 传入裁剪后的 ROI 小图，并将它用于复杂关系或低置信度复核。

## SAM2 线束分割

SAM2.1 Hiera Small 运行在 `9025`。当前 RTX 4060 Ti 显存已被 Qwen 与 Grounding 占用，默认使用 CPU，避免服务同时运行时 OOM：

```powershell
git clone --depth 1 https://github.com/facebookresearch/sam2.git third_party\sam2
$env:SAM2_BUILD_CUDA="0"
.\.venv\Scripts\python.exe -m pip install --no-build-isolation -e third_party\sam2
.\start-sam2.ps1
```

自动解析链路为：

```text
Grounding DINO 生成黑色、灰色、橙色和低压线束粗框
-> SAM2 根据粗框生成像素掩膜
-> 与橙色 HSV 快速分割去重合并
-> 工作台叠加青色 SAM2 轮廓和橙色颜色轮廓
```

Qwen3-VL 需要较新的 Transformers；官方模型说明要求 `transformers>=4.57.0`，服务依赖已单独放在 `qwen_vl_service/requirements.txt`。

## 下一阶段

1. 完成产品世界模型可视化编辑器，并把 ROI 映射为场景对象。
2. 完成配方版本复制、测试、发布、归档和审计。
3. 批量生成视觉标准库 Embedding，支持 Top-K 类别聚合。
4. 增加检测执行计划，对 DINOv2、OCR、颜色规则进行批处理。
5. 将 Qwen3-VL 接入低置信度复核链，并增加人工复核状态。

## Detect 接口测试

公开检测接口支持以下五个配方参数：

```text
line            拉线
materialcode    物料号
operation       工序
camera          相机，例如 CAMERA1
picture         第几次拍照，例如 1
```

当只传 `line`、`materialcode`、`operation` 时，平台会从图片名称中的
`CAMERA数字PICTURE数字` 自动补全相机和拍照次数。

使用固定测试图片调用接口：

```powershell
.\.venv\Scripts\python.exe scripts\test_detect.py `
  --line LINE01 `
  --materialcode MAT001 `
  --operation OP20
```

测试脚本默认使用：

```text
C:\Users\Administrator\Desktop\vision-platform\test_images\ASSY-CAMERA1PICTURE1.png
```
