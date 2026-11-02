# 场景、数据集与模型模块交接说明

## 目标

新模块把“配方中的固定 ROI”和“如何检测该 ROI”分离：

```text
配方 / ROI（在哪检查）
        ↓ 仅关联已发布版本
检测场景（用什么流程检查）
        ↓
原始执行记录 → 独立 VLM 复核 → 人工复判
```

现有 `POST /api/detect` 保持兼容。未关联场景的 ROI 继续执行旧的存在、颜色、OCR 规则；关联了已发布场景的 ROI 优先走新场景运行时。

## 新页面

| 页面 | 地址 | 用途 |
|---|---|---|
| 场景管理 | `/scenes` | 场景维护、测试、评测和提示词优化任务 |
| 数据集管理 | `/datasets` | TEST / TRAIN 数据集、素材上传和 OK/NG 真值 |
| 模型中心 | `/models` | OpenAI 兼容 VLM、YOLO 模型定义和异步任务 |
| 配方工作台 | `/workspace` | ROI 关联已发布场景版本，保留旧规则兼容路径 |

## 检测场景

`InspectionScenario` 是业务检测场景，和原有 `ProductScene`（产品空间模型）不同，避免名称和职责混淆。

场景创建方式：

- `VLM_DIRECT`：一个已配置 VLM 加一份提示词，适合快速验证。
- `WORKFLOW`：保存开始、VLM、YOLO、规则和结束节点。V1 已支持节点数据引用、VLM、已发布 YOLO 模型推理和规则节点。

场景版本状态：

- `DRAFT`：可编辑、测试。
- `PUBLISHED`：可被 ROI 关联、可用于生产与评测。
- `ARCHIVED`：同一场景发布新版本后旧版本自动归档，历史执行记录仍指向旧版本。

ROI 绑定表是 `roi_scenario_bindings`，每个 ROI 至多有一条启用绑定。后端强制拒绝关联非 `PUBLISHED` 版本。

## OpenAI 兼容 VLM

VLM 管理使用标准接口：

```text
GET  {base_url}/v1/models
POST {base_url}/v1/chat/completions
```

`base_url` 可填写根地址或已带 `/v1` 的地址。平台不会在 API 响应中返回 API Key。

推荐配置 API Key 的方式：

1. 在服务器环境变量中设置，例如 `OPENAI_API_KEY`。
2. 在模型中心填写环境变量名 `OPENAI_API_KEY`。

若必须保存 Key，先生成并配置密钥：

```bash
python scripts/generate_vlm_secret.py
# 将输出填入 .env：VLM_SECRET_KEY=...
```

随后 UI 输入的 API Key 使用 Fernet 加密后保存到 SQLite。首次部署必须执行：

```bash
python -m pip install -r requirements.txt
```

其中 `cryptography` 是 API Key 加密保存所需依赖。没有 `VLM_SECRET_KEY` 时，仍可使用无鉴权接口或环境变量方式。

## 数据集与真值

| 用途 | 字段 | 约束 |
|---|---|---|
| 测试 | `purpose=TEST` | 每个参与准确率的样本必须人工标注 `OK` 或 `NG` |
| 训练 | `purpose=TRAIN` | 必须完成与模型任务匹配的分类、检测或分割标注，才可进入 YOLO 训练队列 |

场景评测创建时会把当前数据集修订号和样本 ID 快照写入任务，避免评测途中继续上传素材影响已开始的结果。

## 生产结果与复核

生产检测每次场景运行写入 `scenario_executions`：

- 原始场景结果、分数、输入、输出、耗时不可被复核覆盖。
- 独立 VLM 复核写入 `scenario_execution_reviews`。
- 人工复判写入同一复核记录的 `manual_*` 字段，并拥有最高展示优先级。

不要把“原始结果与 VLM 一致”称为准确率。真实准确率只能来自人工复判、下游质量结果或已标注测试集。

为避免同一模型自证，场景运行时会跳过“主 VLM 与复核 VLM 完全相同”的独立复核，并在记录中写明原因。

## 自动化 Worker

服务启动后会启动一个单任务 worker：

- `SCENE_EVALUATION`：逐个运行 TEST 数据集，输出准确率、漏判（`NG → OK`）和误判（`OK → NG`）。
- `VLM_REVIEW`：每个场景执行记录最多创建一项独立 VLM 复核。
- `PROMPT_OPTIMIZATION`：基于测试集循环生成候选提示词，达到目标准确率、达到最大轮次或人工取消即停止。只记录最佳候选提示词，不会自动覆盖已发布场景。
- `YOLO_TRAINING`：先检查 GPU 空闲显存；满足条件后冻结数据清单、导出 YOLO 格式、运行本地 Ultralytics 训练，并登记草稿权重、曲线和指标。标注或权重不完整时会明确失败，不会伪造训练成功。

SQLite 本地模式一次处理一个任务。迁移至 Linux / PostgreSQL 后，可把同一 `automation_jobs` 表交给独立 worker 进程处理。

## 当前限制与下一阶段

1. 流程画布已保存节点与参数，V1 运行时按 `sort_order` 线性执行；完整 DAG 并行、分支与基于连线的数据映射将在下一阶段启用。
2. YOLO 已支持分类、目标检测和目标分割的本地训练与发布权重推理；当前标注入口为结构化 JSON，图形化框选 / 多边形标注器仍是后续体验优化项。
3. 提示词优化支持直接 VLM 场景；工作流场景需要按节点定义优化目标，暂不自动优化。
4. 外部 `POST /api/detect` 的请求和顶层字段不变。生产系统是否将内部 `ERROR` 统一映射为外部 `NG`，需在联调环境按供应商协议确认后再启用。
