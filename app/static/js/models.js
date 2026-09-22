(() => {
  const api = "/api/v1";
  const state = { vlms: [], visionModels: [], datasets: [], jobs: [] };
  const byId = (id) => document.getElementById(id);
  const escapeHtml = (value) => String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
  const request = async (url, options = {}) => {
    const response = await fetch(url, options);
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(payload.detail || payload.message || "请求失败");
    return payload;
  };
  const notify = (message, type = "success") => {
    byId("modelAlert").innerHTML = `<div class="alert alert-${type} alert-dismissible fade show">${escapeHtml(message)}<button class="btn-close" data-bs-dismiss="alert"></button></div>`;
  };
  const statusClass = (status) => String(status || "").toLowerCase() === "published" || String(status || "").toLowerCase() === "completed"
    ? "published"
    : String(status || "").toLowerCase() === "failed" ? "error" : "draft";
  const modal = (id) => window.bootstrap.Modal.getOrCreateInstance(byId(id));
  const formatDateTime = (value) => {
    if (!value) return "—";
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleString("zh-CN", { hour12: false });
  };
  const taskLabel = (taskType) => ({
    YOLO_DETECTION: "YOLO 目标检测",
    YOLO_SEGMENTATION: "YOLO 目标分割",
    YOLO_CLASSIFICATION: "YOLO 分类",
  }[taskType] || taskType);
  const trainingMetricLabel = (key) => ({
    "metrics/mAP50-95(B)": "mAP50-95（检测）",
    "metrics/mAP50(B)": "mAP50（检测）",
    "metrics/precision(B)": "精确率（检测）",
    "metrics/recall(B)": "召回率（检测）",
    "metrics/mAP50-95(M)": "mAP50-95（分割）",
    "metrics/mAP50(M)": "mAP50（分割）",
    "metrics/precision(M)": "精确率（分割）",
    "metrics/recall(M)": "召回率（分割）",
    "metrics/accuracy_top1": "Top-1 准确率",
    "metrics/accuracy_top5": "Top-5 准确率",
    fitness: "综合适应度",
  }[key] || key);
  const trainingMetricGuide = (key) => ({
    "metrics/mAP50-95(B)": [
      "检测框在多个 IoU 阈值下的平均精度，反映整体定位与分类能力。",
      "越高越好；通常比 mAP50 更严格。若两者差距很大，说明框定位边界还不稳定。",
    ],
    "metrics/mAP50(B)": [
      "检测框在 IoU=0.50 时的平均精度，适合快速观察检测能力。",
      "越高越好；不能单独作为上线依据，应结合 mAP50-95、召回率和混淆矩阵。",
    ],
    "metrics/precision(B)": [
      "模型报出的目标中，真正正确目标所占比例，主要反映误检风险。",
      "越高越好；若很高但召回率低，可能是模型过于保守、存在漏检。",
    ],
    "metrics/recall(B)": [
      "真实目标被模型找到的比例，主要反映漏检风险。",
      "越高越好；工业错漏混场景应重点关注。低召回通常意味着容易漏判。",
    ],
    "metrics/mAP50-95(M)": [
      "分割掩码在多个 IoU 阈值下的平均精度，反映轮廓质量。",
      "越高越好；若明显低于 mAP50(M)，说明掩码边界不够稳定。",
    ],
    "metrics/mAP50(M)": [
      "分割掩码在 IoU=0.50 时的平均精度，用于快速检查分割效果。",
      "越高越好；应配合样本可视化检查边缘、断裂和粘连区域。",
    ],
    "metrics/precision(M)": [
      "分割结果中正确目标的比例，主要反映多分或错分风险。",
      "越高越好；需同时观察召回率，避免模型只保留少量高把握目标。",
    ],
    "metrics/recall(M)": [
      "真实分割目标被识别出的比例，主要反映漏分风险。",
      "越高越好；低值应优先检查漏标、样本覆盖和目标尺寸。",
    ],
    "metrics/accuracy_top1": [
      "分类任务中，最高分预测与真实类别一致的比例。",
      "越高越好；要结合每类样本量与混淆矩阵，不要只看总平均值。",
    ],
    "metrics/accuracy_top5": [
      "真实类别出现在预测前五名中的比例，反映类别可区分性。",
      "通常应不低于 Top-1；若两者差距很大，说明模型常把正确类排在较后位置。",
    ],
    fitness: [
      "训练框架用于选择最佳权重的综合分数，通常由多个验证指标加权得到。",
      "仅用于同一训练任务内比较轮次；不同模型或数据集之间不建议直接横向比较。",
    ],
  }[key] || [
    "训练框架输出的原始指标。",
    "请结合模型类型、验证集样本量、训练曲线和实际图片复测共同判断。",
  ]);
  const trainingMetricValue = (key, value) => {
    if (value === undefined || value === null || value === "") return "—";
    if (typeof value === "number" && /(?:mAP|precision|recall|accuracy|fitness)/i.test(key)) return `${(value * 100).toFixed(2)}%`;
    if (typeof value === "number") return Number.isInteger(value) ? String(value) : value.toFixed(4);
    return String(value);
  };
  const visionCatalog = {
    YOLO: {
      tasks: [
        ["YOLO_DETECTION", "YOLO 目标检测"],
        ["YOLO_SEGMENTATION", "YOLO 目标分割"],
        ["YOLO_CLASSIFICATION", "YOLO 分类"],
      ],
      basesByTask: {
        YOLO_DETECTION: ["yolo11n.pt", "yolo11s.pt", "yolo11m.pt", "yolov8n.pt", "yolov8s.pt"],
        YOLO_SEGMENTATION: ["yolo11n-seg.pt", "yolo11s-seg.pt", "yolo11m-seg.pt"],
        YOLO_CLASSIFICATION: ["yolo11n-cls.pt", "yolo11s-cls.pt", "yolo11m-cls.pt"],
      },
    },
  };

  function renderVlms() {
    byId("vlmModelList").innerHTML = state.vlms.length
      ? state.vlms.map((model) => `
        <article class="model-card catalog-model-card">
          <div class="model-card-top">
            <div class="model-card-title"><span class="model-type-icon">◈</span><div><small>${escapeHtml(model.code)}</small><strong>${escapeHtml(model.name)}</strong></div></div>
            <span class="status-pill ${model.enabled ? "published" : "error"}">${model.enabled ? "启用" : "停用"}</span>
          </div>
          <div class="model-kind">VLM · OpenAI 兼容接口</div>
          <dl class="model-detail-list">
            <div><dt>模型 ID</dt><dd>${escapeHtml(model.model_name)}</dd></div>
            <div><dt>服务地址</dt><dd title="${escapeHtml(model.base_url)}">${escapeHtml(model.base_url)}</dd></div>
            <div><dt>鉴权方式</dt><dd>${escapeHtml(model.api_key_hint || "无鉴权")}</dd></div>
            <div><dt>思考模式</dt><dd>${model.thinking_enabled ? "已启用" : "未启用"}</dd></div>
          </dl>
          <div class="model-card-actions">
            <button class="btn btn-sm btn-outline-secondary" data-edit-vlm="${model.id}" type="button">编辑</button>
            <button class="btn btn-sm btn-outline-primary" data-test-vlm="${model.id}" type="button">测试连接</button>
            <button class="btn btn-sm btn-outline-danger" data-delete-vlm="${model.id}" type="button">删除</button>
          </div>
          <pre class="job-result model-test-result" id="vlmTest_${model.id}" hidden></pre>
        </article>`).join("")
      : `<div class="empty-state catalog-empty"><span>◈</span><strong>还没有 VLM 模型</strong><p>点击“创建模型”，接入本地或云端 OpenAI 兼容接口。</p><button class="btn btn-primary btn-sm" type="button" data-create-model="vlm">创建 VLM 模型</button></div>`;
  }

  function renderVisionModels() {
    byId("visionModelList").innerHTML = state.visionModels.length
      ? state.visionModels.map((model) => {
        const versions = model.versions || [];
        const publishedVersion = versions.find((version) => version.status === "PUBLISHED");
        const publishableVersion = versions.find((version) => version.status === "DRAFT" && version.weights_path);
        return `
          <article class="model-card catalog-model-card">
            <div class="model-card-top">
              <div class="model-card-title"><span class="model-type-icon">◉</span><div><small>${escapeHtml(model.code)}</small><strong>${escapeHtml(model.name)}</strong></div></div>
              <span class="status-pill ${model.enabled ? "published" : "error"}">${model.enabled ? "启用" : "停用"}</span>
            </div>
            <div class="model-kind">${escapeHtml(taskLabel(model.task_type))}</div>
            <dl class="model-detail-list">
              <div><dt>基础模型</dt><dd>${escapeHtml(model.base_model || "未设置")}</dd></div>
              <div><dt>识别类别</dt><dd>${escapeHtml((model.labels_json || []).join("、") || "未设置")}</dd></div>
              <div><dt>模型版本</dt><dd>${versions.length} 个${publishedVersion ? ` · 已发布 ${escapeHtml(publishedVersion.version)}` : " · 暂未发布"}</dd></div>
            </dl>
            <div class="model-card-actions">
              <button class="btn btn-sm btn-outline-secondary" data-edit-vision="${model.id}" type="button">编辑</button>
              <button class="btn btn-sm btn-outline-secondary" data-toggle-vision-versions="${model.id}" type="button">查看版本</button>
              <button class="btn btn-sm btn-outline-primary" data-train-vision="${model.id}" type="button">创建训练任务</button>
              ${publishableVersion ? `<button class="btn btn-sm btn-primary" data-publish-vision-version="${publishableVersion.id}" data-model-id="${model.id}" type="button">发布 V${escapeHtml(publishableVersion.version)}</button>` : ""}
              <button class="btn btn-sm btn-outline-danger" data-delete-vision="${model.id}" type="button">删除</button>
            </div>
            <pre class="job-result model-test-result" id="visionVersions_${model.id}" hidden>${escapeHtml(JSON.stringify(versions, null, 2))}</pre>
          </article>`;
      }).join("")
      : `<div class="empty-state catalog-empty"><span>◉</span><strong>还没有训练模型</strong><p>先创建 YOLO 模型卡片，再在模型训练页关联数据集训练版本。</p><button class="btn btn-primary btn-sm" type="button" data-create-model="vision">创建训练模型</button></div>`;
  }

  function trainingJobDescription(job) {
    const result = job.result_json || {};
    if (job.status === "WAITING_GPU") {
      const free = Array.isArray(result.available_gpu_memory_mb) && result.available_gpu_memory_mb.length
        ? `${Math.max(...result.available_gpu_memory_mb)} MB`
        : "暂不可用";
      const required = Number(result.required_gpu_memory_mb || job.config_json?.gpu_memory_required_mb || 0);
      const reserve = Number(result.reserve_gpu_memory_mb || 0);
      return `等待 GPU：当前最大可用 ${free}；本任务至少需要 ${required + reserve} MB（训练 ${required} MB + 安全预留 ${reserve} MB）。`;
    }
    if (job.status === "RUNNING") return "正在训练，完成后会自动写入权重、指标曲线和草稿模型版本。";
    if (job.status === "FAILED") return job.error_message || "训练失败，请查看任务详情和运行日志。";
    if (job.status === "COMPLETED") return result.message || "训练完成，已生成草稿模型版本。";
    if (job.status === "CANCELED") return "任务已取消，未生成训练版本。";
    return "已进入训练调度队列。";
  }

  function trainingMetrics(job) {
    return job.result_json?.model_version?.metrics_json || {};
  }

  function renderJobs() {
    const trainingJobs = state.jobs.filter((job) => String(job.job_type || "").endsWith("_TRAINING"));
    if (!trainingJobs.length) {
      byId("automationJobList").innerHTML = '<div class="empty-task-state"><strong>还没有模型训练任务</strong><p>点击右上角“创建模型训练任务”，保存后任务会自动显示在这里。</p></div>';
      return;
    }
    const rows = trainingJobs.map((job) => {
      const split = job.input_snapshot_json?.dataset_split || job.result_json?.dataset_split;
      const metrics = trainingMetrics(job);
      const splitText = split ? `训 ${split.counts?.TRAIN ?? 0} / 验 ${split.counts?.VAL ?? 0} / 测 ${split.counts?.TEST ?? 0}` : "待切分";
      const mapKey = Object.keys(metrics).find((key) => key.includes("mAP50-95"))
        || Object.keys(metrics).find((key) => key.includes("mAP50"))
        || Object.keys(metrics).find((key) => key.includes("accuracy_top1"));
      const canCancel = ["QUEUED", "WAITING_GPU"].includes(job.status);
      return `<tr>
        <td>#${job.id}</td>
        <td>${escapeHtml(taskLabel(job.input_snapshot_json?.model_task_type || job.job_type.replace(/_TRAINING$/, "")))}</td>
        <td>${escapeHtml(job.config_json?.version || "—")}</td>
        <td>${escapeHtml(splitText)}</td>
        <td>${mapKey ? escapeHtml(trainingMetricValue(mapKey, metrics[mapKey])) : "—"}</td>
        <td class="training-job-description" title="${escapeHtml(trainingJobDescription(job))}">${escapeHtml(trainingJobDescription(job))}</td>
        <td><span class="status-pill ${statusClass(job.status)}">${escapeHtml(job.status === "WAITING_GPU" ? "等待 GPU" : job.status)}</span></td>
        <td>${escapeHtml(formatDateTime(job.completed_at || job.created_at))}</td>
        <td><div class="table-action-group"><button class="btn btn-sm btn-outline-secondary" data-training-job-detail="${job.id}" type="button">详情</button>${canCancel ? `<button class="btn btn-sm btn-outline-danger" data-cancel-job="${job.id}" type="button">取消</button>` : ""}</div></td>
      </tr>`;
    }).join("");
    byId("automationJobList").innerHTML = `<div class="task-table-wrap"><table class="task-table training-job-table"><thead><tr><th>任务</th><th>类型</th><th>版本</th><th>数据切分</th><th>核心指标</th><th>任务说明</th><th>状态</th><th>时间</th><th>操作</th></tr></thead><tbody>${rows}</tbody></table></div>`;
  }

  function fillTrainingOptions({ preferredModelId = null, preferredDatasetId = null } = {}) {
    const modelSelect = byId("trainingVisionModel");
    const datasetSelect = byId("trainingDataset");
    const selectedModelId = String(preferredModelId ?? modelSelect.value ?? "");
    const selectedDatasetId = String(preferredDatasetId ?? datasetSelect.value ?? "");
    modelSelect.innerHTML = `<option value="">请选择视觉模型</option>${state.visionModels.filter((item) => item.enabled).map((item) => `<option value="${item.id}">${escapeHtml(item.name)} · ${escapeHtml(taskLabel(item.task_type))}</option>`).join("")}`;
    datasetSelect.innerHTML = `<option value="">请选择训练数据集</option>${state.datasets.filter((item) => item.purpose === "TRAIN" && item.enabled).map((item) => `<option value="${item.id}">${escapeHtml(item.name)} · r${item.revision}</option>`).join("")}`;
    if ([...modelSelect.options].some((option) => option.value === selectedModelId)) modelSelect.value = selectedModelId;
    if ([...datasetSelect.options].some((option) => option.value === selectedDatasetId)) datasetSelect.value = selectedDatasetId;
    updateTrainingSelectionHint();
  }

  function updateTrainingSelectionHint() {
    const modelId = Number(byId("trainingVisionModel").value || 0);
    const datasetId = Number(byId("trainingDataset").value || 0);
    const model = state.visionModels.find((item) => item.id === modelId);
    const dataset = state.datasets.find((item) => item.id === datasetId);
    const hint = byId("trainingSelectionHint");
    if (!model && !dataset) {
      hint.textContent = "请选择模型和数据集；训练数据会在首次提交时自动按 70% / 20% / 10% 切分。";
      return;
    }
    const modelText = model ? `${model.name}（${taskLabel(model.task_type)}）` : "未选择模型";
    const datasetText = dataset
      ? `${dataset.name}，当前 ${dataset.item_count ?? 0} 个有效素材`
      : "未选择训练数据集";
    hint.textContent = `${modelText} · ${datasetText}。提交时会固定本次训练 / 验证 / 测试样本清单，后续重复训练保持可复现。`;
  }

  function openTrainingTask(modelId = null) {
    if (byId("modelsPage").dataset.activeModelView !== "training") {
      const suffix = modelId ? `?model_id=${encodeURIComponent(modelId)}` : "";
      window.location.assign(`/models/training${suffix}`);
      return;
    }
    const form = byId("trainingJobForm");
    form.reset();
    byId("trainingVersion").value = "1.0";
    byId("trainingEpochs").value = "100";
    byId("trainingImageSize").value = "640";
    byId("trainingBatchSize").value = "";
    byId("trainingMemory").value = "4096";
    fillTrainingOptions({ preferredModelId: modelId });
    modal("trainingJobModal").show();
  }

  async function loadData() {
    const [vlms, visionModels, datasets, jobs] = await Promise.all([
      request(`${api}/vlm-models`),
      request(`${api}/vision-models`),
      request(`${api}/datasets`),
      request(`${api}/automation-jobs`),
    ]);
    state.vlms = vlms;
    state.visionModels = visionModels;
    state.datasets = datasets;
    state.jobs = jobs;
    renderVlms();
    renderVisionModels();
    renderJobs();
    fillTrainingOptions();
  }

  function openVlmModal(model = null) {
    const form = byId("vlmModelForm");
    form.reset();
    byId("vlmEditId").value = model?.id || "";
    form.elements.code.readOnly = Boolean(model);
    byId("vlmModelModalTitle").textContent = model ? "编辑 VLM 模型" : "创建 VLM 模型";
    byId("saveVlmModel").textContent = model ? "保存修改" : "保存 VLM 模型";
    if (model) {
      form.elements.code.value = model.code;
      form.elements.name.value = model.name;
      form.elements.base_url.value = model.base_url;
      form.elements.model_name.value = model.model_name;
      form.elements.api_key_env_name.value = model.api_key_env_name || "";
      form.elements.thinking_enabled.checked = Boolean(model.thinking_enabled);
      form.elements.temperature.value = model.temperature;
      form.elements.max_tokens.value = model.max_tokens;
      form.elements.timeout_seconds.value = model.timeout_seconds;
      form.elements.extra_params_json.value = Object.keys(model.extra_params_json || {}).length
        ? JSON.stringify(model.extra_params_json, null, 2)
        : "";
    }
    modal("vlmModelModal").show();
  }

  function openVisionModal(model = null) {
    const form = byId("visionModelForm");
    form.reset();
    byId("visionEditId").value = model?.id || "";
    form.elements.code.readOnly = Boolean(model);
    byId("visionModelModalTitle").textContent = model ? "编辑训练模型" : "创建训练模型";
    form.querySelector('[type="submit"]').textContent = model ? "保存修改" : "保存训练模型";
    syncVisionFamily({ taskType: model?.task_type, baseModel: model?.base_model });
    if (model) {
      form.elements.code.value = model.code;
      form.elements.name.value = model.name;
      form.elements.labels.value = (model.labels_json || []).join("\n");
      form.elements.description.value = model.description || "";
    }
    modal("visionModelModal").show();
  }

  function syncVisionFamily({ taskType = null, baseModel = null } = {}) {
    const catalog = visionCatalog.YOLO;
    const taskSelect = byId("visionModelTaskType");
    const baseInput = byId("visionBaseModel");
    const baseOptions = byId("visionBaseModelOptions");
    const selectedTask = taskType || taskSelect.value || catalog.tasks[0][0];
    taskSelect.innerHTML = catalog.tasks
      .map(([value, label]) => `<option value="${value}">${label}</option>`)
      .join("");
    taskSelect.value = catalog.tasks.some(([value]) => value === selectedTask)
      ? selectedTask
      : catalog.tasks[0][0];
    const bases = catalog.basesByTask[taskSelect.value] || [];
    baseOptions.innerHTML = bases
      .map((value) => `<option value="${value}"></option>`)
      .join("");
    baseInput.value = baseModel || bases[0] || "";
  }

  async function saveVlm(event) {
    event.preventDefault();
    const form = byId("vlmModelForm");
    const editId = Number(byId("vlmEditId").value || 0);
    const values = Object.fromEntries(new FormData(form).entries());
    let extra = {};
    try {
      extra = values.extra_params_json?.trim() ? JSON.parse(values.extra_params_json) : {};
    } catch {
      notify("扩展参数必须为有效 JSON。", "warning");
      return;
    }
    const payload = {
      ...values,
      thinking_enabled: form.elements.thinking_enabled.checked,
      temperature: Number(values.temperature),
      max_tokens: Number(values.max_tokens),
      timeout_seconds: Number(values.timeout_seconds),
      extra_params_json: extra,
    };
    delete payload.extra_params_json;
    payload.extra_params_json = extra;
    if (!payload.api_key) delete payload.api_key;
    if (!payload.api_key_env_name) delete payload.api_key_env_name;
    if (editId) delete payload.code;
    try {
      await request(editId ? `${api}/vlm-models/${editId}` : `${api}/vlm-models`, {
        method: editId ? "PUT" : "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      modal("vlmModelModal").hide();
      if (!editId && byId("modelsPage").dataset.activeModelView !== "vlm") {
        window.location.assign("/models/vlm");
        return;
      }
      await loadData();
      notify(editId ? "VLM 模型已更新。" : "VLM 模型已创建。卡片已加入模型中心。");
    } catch (error) {
      notify(error.message, "danger");
    }
  }

  async function saveVision(event) {
    event.preventDefault();
    const form = byId("visionModelForm");
    const editId = Number(byId("visionEditId").value || 0);
    const values = Object.fromEntries(new FormData(form).entries());
    const payload = {
      ...values,
      labels_json: String(values.labels || "").split(/\r?\n/).map((item) => item.trim()).filter(Boolean),
    };
    delete payload.labels;
    delete payload.model_family;
    if (editId) delete payload.code;
    try {
      await request(editId ? `${api}/vision-models/${editId}` : `${api}/vision-models`, {
        method: editId ? "PUT" : "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      modal("visionModelModal").hide();
      if (byId("modelsPage").dataset.activeModelView !== "registry") {
        window.location.assign("/models/registry");
        return;
      }
      await loadData();
      notify(editId ? "训练模型已更新。" : "训练模型已创建。卡片已加入训练模型维护页面。");
    } catch (error) {
      notify(error.message, "danger");
    }
  }

  async function testVlm(id) {
    const output = byId(`vlmTest_${id}`);
    output.hidden = false;
    output.textContent = "正在测试连接…";
    try {
      const result = await request(`${api}/vlm-models/${id}/test`, { method: "POST" });
      output.textContent = JSON.stringify(result, null, 2);
      notify(result.ok ? "VLM 连接成功。" : `VLM 连接失败：${result.message}`, result.ok ? "success" : "warning");
    } catch (error) {
      output.textContent = error.message;
      notify(error.message, "danger");
    }
  }

  async function deleteVlm(id) {
    if (!confirm("删除后场景不能继续引用该 VLM 配置。确认删除吗？")) return;
    try {
      await request(`${api}/vlm-models/${id}`, { method: "DELETE" });
      await loadData();
      notify("VLM 模型已删除。");
    } catch (error) {
      notify(error.message, "danger");
    }
  }

  function openTrainingForModel(modelId) {
    openTrainingTask(modelId);
  }

  function trainingArtifactCards(artifactUrls) {
    const artifacts = [
      ["training_curve", "训练曲线", "用于观察训练集与验证集指标及损失趋势，持续分离时需要检查是否过拟合。"],
      ["confusion_matrix", "混淆矩阵", "用于检查各类别之间的混淆情况和容易误判的类别。"],
      ["labels_preview", "标注预览", "用于确认训练前的类别与标注框是否符合预期。"],
    ].filter(([key]) => artifactUrls?.[key]);
    if (!artifacts.length) {
      return '<div class="artifact-empty-state">训练图表将在任务完成且训练框架产出相应文件后显示。等待 GPU、训练中或历史任务未保留图表时，此处会保持为空。</div>';
    }
    return `<div class="training-artifact-grid">${artifacts.map(([key, title, description]) => `<figure class="training-artifact-card"><img class="training-artifact-image" src="${escapeHtml(artifactUrls[key])}" alt="${escapeHtml(title)}" loading="lazy"><figcaption><strong>${escapeHtml(title)}</strong><span>${escapeHtml(description)}</span></figcaption></figure>`).join("")}</div>`;
  }

  function trainingMetricsMarkup(metrics) {
    const entries = Object.entries(metrics || {});
    if (!entries.length) return '<div class="artifact-empty-state">任务完成后会展示训练框架返回的指标。当前尚无可用指标。</div>';
    const cards = entries.slice(0, 8).map(([key, value]) => `<div><small>${escapeHtml(trainingMetricLabel(key))}</small><strong>${escapeHtml(trainingMetricValue(key, value))}</strong></div>`).join("");
    const rows = entries.map(([key, value]) => {
      const [description, guidance] = trainingMetricGuide(key);
      return `<tr><td>${escapeHtml(trainingMetricLabel(key))}</td><td>${escapeHtml(trainingMetricValue(key, value))}</td><td>${escapeHtml(description)}</td><td>${escapeHtml(guidance)}</td></tr>`;
    }).join("");
    return `<div class="task-metric-grid training-metric-grid">${cards}</div><div class="task-table-wrap training-metrics-table-wrap"><table class="task-table training-metrics-table"><thead><tr><th>指标</th><th>值</th><th>说明</th><th>如何判断是否合理</th></tr></thead><tbody>${rows}</tbody></table></div>`;
  }

  async function openTrainingJobDetail(jobId) {
    let job = state.jobs.find((item) => item.id === Number(jobId));
    if (!job) return;
    try {
      const freshJob = await request(`${api}/automation-jobs/${jobId}`);
      job = freshJob;
      state.jobs = state.jobs.map((item) => item.id === job.id ? job : item);
      renderJobs();
    } catch (error) {
      notify(`无法刷新训练任务详情：${error.message}`, "warning");
    }
    const config = job.config_json || {};
    const result = job.result_json || {};
    const snapshot = job.input_snapshot_json || {};
    const split = snapshot.dataset_split || result.dataset_split || {};
    const model = state.visionModels.find((item) => item.id === Number(job.vision_model_id));
    const metrics = trainingMetrics(job);
    const version = result.model_version || {};
    const splitCounts = split.counts || {};
    const gpuMemory = Array.isArray(result.available_gpu_memory_mb) && result.available_gpu_memory_mb.length
      ? `${result.available_gpu_memory_mb.join(" / ")} MB`
      : "—";
    byId("trainingJobDetailTitle").textContent = `模型训练任务 #${job.id}`;
    byId("trainingJobDetailMeta").innerHTML = [
      ["任务状态", job.status === "WAITING_GPU" ? "等待 GPU" : job.status],
      ["训练模型", model ? model.name : `模型 #${job.vision_model_id || "—"}`],
      ["训练版本", config.version || version.version || "—"],
      ["训练数据集", state.datasets.find((item) => item.id === Number(job.dataset_id))?.name || `数据集 #${job.dataset_id || "—"}`],
      ["创建时间", formatDateTime(job.created_at)],
      ["完成时间", formatDateTime(job.completed_at)],
    ].map(([label, value]) => `<div><small>${escapeHtml(label)}</small><strong title="${escapeHtml(value)}">${escapeHtml(value)}</strong></div>`).join("");
    const error = job.error_message ? `<div class="task-error">${escapeHtml(job.error_message)}</div>` : "";
    const configuration = [
      ["任务类型", taskLabel(snapshot.model_task_type || job.job_type.replace(/_TRAINING$/, ""))],
      ["基础权重", model?.base_model || "—"],
      ["训练轮数", config.epochs || "—"],
      ["输入尺寸", config.image_size ? `${config.image_size}px` : "—"],
      ["批大小", config.batch_size || "自动"],
      ["显存要求", config.gpu_memory_required_mb ? `${config.gpu_memory_required_mb} MB` : "—"],
      ["实际 GPU", result.selected_gpu_index === undefined ? "—" : `GPU ${result.selected_gpu_index}`],
      ["检测到的可用显存", gpuMemory],
      ["数据切分", split.counts ? `训练 ${splitCounts.TRAIN ?? 0} / 验证 ${splitCounts.VAL ?? 0} / 测试 ${splitCounts.TEST ?? 0}` : "待切分"],
    ].map(([label, value]) => `<div><small>${escapeHtml(label)}</small><strong>${escapeHtml(value)}</strong></div>`).join("");
    const diagnostic = { task_config: config, dataset_split: split, training_validation: snapshot.training_validation || result.training_validation || {}, result };
    byId("trainingJobDetailContent").innerHTML = `
      <section class="task-result-section"><div class="section-heading"><div><small>RUN STATUS</small><strong>任务执行情况</strong></div></div><p class="task-description-copy">${escapeHtml(trainingJobDescription(job))}</p></section>
      ${error}
      <section class="task-result-section"><div class="section-heading"><div><small>TRAINING CONFIGURATION</small><strong>训练参数与数据切分</strong></div></div><div class="task-detail-grid training-config-grid">${configuration}</div></section>
      <section class="task-result-section"><div class="section-heading"><div><small>TRAINING METRICS</small><strong>训练结果指标</strong></div></div>${trainingMetricsMarkup(metrics)}</section>
      <section class="task-result-section"><div class="section-heading"><div><small>TRAINING DIAGNOSTICS</small><strong>训练图表与过拟合检查</strong></div></div><p class="muted-copy">训练曲线应结合验证集指标判断：若训练指标持续变好、验证指标变差或验证损失持续上升，才是疑似过拟合信号；不能只凭单一图片判断。</p>${trainingArtifactCards(job.artifact_urls || {})}</section>
      <details class="task-details"><summary>查看完整任务参数与原始结果</summary><pre class="job-result">${escapeHtml(JSON.stringify(diagnostic, null, 2))}</pre></details>`;
    modal("trainingJobDetailModal").show();
  }

  async function deleteVision(id) {
    if (!confirm("删除后不能再新建训练任务，已有训练记录仍会保留。确认删除吗？")) return;
    try {
      await request(`${api}/vision-models/${id}`, { method: "DELETE" });
      await loadData();
      notify("训练模型已删除。");
    } catch (error) {
      notify(error.message, "danger");
    }
  }
  async function publishVisionVersion(modelId, versionId) {
    if (!confirm("发布后该版本将可被已发布流程节点引用，当前已发布版本会归档。确认发布吗？")) return;
    try {
      await request(`${api}/vision-models/${modelId}/versions/${versionId}/publish`, { method: "POST" });
      await loadData();
      notify("模型版本已发布，可在流程节点中选择。");
    } catch (error) {
      notify(error.message, "danger");
    }
  }

  async function queueTraining(event) {
    event.preventDefault();
    const modelId = Number(byId("trainingVisionModel").value);
    const datasetId = Number(byId("trainingDataset").value);
    if (!modelId || !datasetId) {
      notify("请选择视觉模型和 TRAIN 数据集。", "warning");
      return;
    }
    const payload = {
      dataset_id: datasetId,
      version: byId("trainingVersion").value.trim(),
      epochs: Number(byId("trainingEpochs").value),
      image_size: Number(byId("trainingImageSize").value),
      gpu_memory_required_mb: Number(byId("trainingMemory").value),
    };
    const batchSize = byId("trainingBatchSize").value.trim();
    if (batchSize) payload.batch_size = Number(batchSize);
    try {
      const result = await request(`${api}/vision-models/${modelId}/training-jobs`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const counts = result.split_summary?.counts;
      const splitText = counts
        ? ` 本次切分：训练 ${counts.TRAIN}、验证 ${counts.VAL}、测试 ${counts.TEST}。`
        : "";
      modal("trainingJobModal").hide();
      await loadData();
      notify(`${result.message || "训练任务已创建。"}${splitText} 已加入模型训练任务列表。`);
    } catch (error) {
      notify(error.message, "danger");
    }
  }

  async function cancelJob(id) {
    try {
      await request(`${api}/automation-jobs/${id}/cancel`, { method: "POST" });
      await loadData();
      notify("任务已从等待队列移除。");
    } catch (error) {
      notify(error.message, "danger");
    }
  }

  document.addEventListener("DOMContentLoaded", () => {
    byId("reloadModels").addEventListener("click", () => loadData().catch((error) => notify(error.message, "danger")));
    byId("vlmModelForm").addEventListener("submit", saveVlm);
    byId("visionModelForm").addEventListener("submit", saveVision);
    byId("trainingJobForm").addEventListener("submit", queueTraining);
    byId("reloadModelJobs").addEventListener("click", () => loadData().catch((error) => notify(error.message, "danger")));
    byId("openTrainingTask")?.addEventListener("click", () => openTrainingTask());
    byId("openTrainingTaskInline")?.addEventListener("click", () => openTrainingTask());

    document.addEventListener("click", (event) => {
      const create = event.target.closest("[data-create-model]");
      if (!create) return;
      if (create.dataset.createModel === "vlm") openVlmModal();
      if (create.dataset.createModel === "vision") openVisionModal();
    });

    byId("vlmModelList").addEventListener("click", (event) => {
      const test = event.target.closest("[data-test-vlm]");
      const edit = event.target.closest("[data-edit-vlm]");
      const remove = event.target.closest("[data-delete-vlm]");
      if (test) testVlm(Number(test.dataset.testVlm));
      if (edit) openVlmModal(state.vlms.find((item) => item.id === Number(edit.dataset.editVlm)));
      if (remove) deleteVlm(Number(remove.dataset.deleteVlm));
    });

    byId("visionModelList").addEventListener("click", (event) => {
      const versions = event.target.closest("[data-toggle-vision-versions]");
      const train = event.target.closest("[data-train-vision]");
      const edit = event.target.closest("[data-edit-vision]");
      const remove = event.target.closest("[data-delete-vision]");
      const publish = event.target.closest("[data-publish-vision-version]");
      if (versions) {
        const output = byId(`visionVersions_${versions.dataset.toggleVisionVersions}`);
        output.hidden = !output.hidden;
      }
      if (train) openTrainingForModel(Number(train.dataset.trainVision));
      if (edit) openVisionModal(state.visionModels.find((item) => item.id === Number(edit.dataset.editVision)));
      if (remove) deleteVision(Number(remove.dataset.deleteVision));
      if (publish) publishVisionVersion(Number(publish.dataset.modelId), Number(publish.dataset.publishVisionVersion));
    });

    byId("automationJobList").addEventListener("click", (event) => {
      const detail = event.target.closest("[data-training-job-detail]");
      const button = event.target.closest("[data-cancel-job]");
      if (detail) openTrainingJobDetail(Number(detail.dataset.trainingJobDetail));
      if (button) cancelJob(Number(button.dataset.cancelJob));
    });

    byId("trainingVisionModel").addEventListener("change", updateTrainingSelectionHint);
    byId("trainingDataset").addEventListener("change", updateTrainingSelectionHint);
    byId("visionModelTaskType").addEventListener("change", (event) => {
      syncVisionFamily({ taskType: event.target.value });
    });
    syncVisionFamily();
    loadData()
      .then(() => {
        const selectedModelId = new URLSearchParams(window.location.search).get("model_id");
        if (
          selectedModelId
          && byId("modelsPage").dataset.activeModelView === "training"
          && state.visionModels.some((item) => item.id === Number(selectedModelId))
        ) {
          openTrainingTask(Number(selectedModelId));
          window.history.replaceState({}, "", "/models/training");
        }
      })
      .catch((error) => notify(error.message, "danger"));
  });
})();
