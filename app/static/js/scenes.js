(() => {
  const api = "/api/v1";
  const state = { scenes: [], vlms: [], visionVersions: [], datasets: [], jobs: [], selectedSceneId: null, selectedVersionId: null };
  const byId = (id) => document.getElementById(id);
  const escapeHtml = (value) => String(value ?? "").replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;").replaceAll('"', "&quot;").replaceAll("'", "&#039;");
  const apiErrorMessage = (payload, fallback) => {
    if (Array.isArray(payload?.detail)) {
      return payload.detail.map((item) => {
        const field = item?.loc?.at?.(-1) || "参数";
        return `${field}：${item?.msg || "格式不正确"}`;
      }).join("；");
    }
    return payload?.detail || payload?.message || fallback;
  };
  const request = async (url, options = {}) => {
    const response = await fetch(url, options);
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(apiErrorMessage(payload, `请求失败（${response.status}）`));
    return payload;
  };
  const notify = (message, type = "success") => { byId("sceneAlert").innerHTML = `<div class="alert alert-${type} alert-dismissible fade show">${escapeHtml(message)}<button class="btn-close" data-bs-dismiss="alert"></button></div>`; };
  const selectedScene = () => state.scenes.find((item) => item.id === state.selectedSceneId);
  const selectedVersion = () => selectedScene()?.versions.find((item) => item.id === state.selectedVersionId);
  const publishedVersions = () => state.scenes.flatMap((scene) => scene.versions.filter((version) => version.status === "PUBLISHED").map((version) => ({ scene, version })));

  function statusClass(status) { const value = String(status || "DRAFT").toLowerCase(); return ["published", "completed"].includes(value) ? "published" : ["failed", "canceled"].includes(value) ? "error" : "draft"; }
  function modeLabel(mode) { return mode === "WORKFLOW" ? "流程检测" : "VLM 检测"; }
  function nodeTypeLabel(nodeType) { return ({ START: "开始", END: "结束", VLM: "VLM 检测", VISION_MODEL: "模型检测", RULE: "规则判断" }[String(nodeType || "").toUpperCase()] || nodeType); }
  function fillVlmSelect(id, selected) {
    byId(id).innerHTML = `<option value="">不选择</option>${state.vlms.filter((item) => item.enabled).map((item) => `<option value="${item.id}" ${Number(selected) === item.id ? "selected" : ""}>${escapeHtml(item.name)} · ${escapeHtml(item.model_name)}</option>`).join("")}`;
  }
  function fillVisionModelSelect(selected) {
    const available = state.visionVersions.filter((item) => item.available);
    byId("workflowVisionModelVersion").innerHTML = `<option value="">请选择已发布模型版本</option>${available.map((item) => `<option value="${item.version_id}" ${Number(selected) === item.version_id ? "selected" : ""}>${escapeHtml(item.model_name)} · V${escapeHtml(item.version)} · ${escapeHtml(item.task_type)}</option>`).join("")}`;
  }
  function syncWorkflowNodeForm() {
    const nodeType = byId("workflowNodeType").value;
    const isVlm = nodeType === "VLM";
    const isVision = nodeType === "VISION_MODEL";
    byId("workflowVlmField").hidden = !isVlm;
    byId("workflowVisionModelField").hidden = !isVision;
    if (isVlm) {
      byId("workflowNodeConfig").placeholder = '{"prompt":"只检查图片中的线束是否锁付；看不清返回 UNCERTAIN"}';
      byId("workflowNodeConfigHint").textContent = "提示词可引用 {{ input.* }} 与 {{ nodes.节点键.* }}。VLM 模型由上方选择。";
    } else if (isVision) {
      byId("workflowNodeConfig").placeholder = '{"expected_min_count":1,"confidence":0.25,"iou":0.45}';
      byId("workflowNodeConfigHint").textContent = "可设置 expected_count / expected_min_count / expected_max_count、expected_class、confidence、iou。模型版本由上方选择。";
    } else if (nodeType === "RULE") {
      byId("workflowNodeConfig").placeholder = '{"actual":"{{ nodes.detector.detection_count }}","operator":"NUMBER_GTE","expected":1}';
      byId("workflowNodeConfigHint").textContent = "规则支持 EQUALS、CONTAINS、EXISTS、NUMBER_EQUALS、NUMBER_GT、NUMBER_GTE、NUMBER_LT、NUMBER_LTE。";
    } else if (nodeType === "END") {
      byId("workflowNodeConfig").placeholder = '{"output":{"result":"{{ nodes.rule.result }}","confidence":"{{ nodes.detector.confidence }}"}}';
      byId("workflowNodeConfigHint").textContent = "结束节点可引用前序节点输出；留空时采用最后一个节点的结果。";
    } else {
      byId("workflowNodeConfig").placeholder = "开始节点通常不需要额外参数。";
      byId("workflowNodeConfigHint").textContent = "开始节点自动提供 image_path 与调用上下文。";
    }
  }
  function syncSceneCreateMode() {
    const direct = byId("sceneCreateMode").value === "VLM_DIRECT";
    byId("sceneCreateVlmFields").hidden = !direct;
    byId("sceneCreateWorkflowInfo").hidden = direct;
    byId("sceneCreatePrimaryVlm").required = direct;
    byId("sceneCreatePrompt").required = direct;
  }
  function openSceneCreate() {
    const form = byId("sceneCreateForm");
    form.reset();
    form.elements.category.value = "HARNESS";
    form.elements.version.value = "1.0";
    fillVlmSelect("sceneCreatePrimaryVlm");
    fillVlmSelect("sceneCreateReviewVlm");
    syncSceneCreateMode();
    openModal("sceneCreateModal");
  }
  function renderSceneList() {
    const keyword = byId("sceneSearch").value.trim().toLowerCase();
    const scenes = state.scenes.filter((item) => !keyword || [item.code, item.name, item.category].join(" ").toLowerCase().includes(keyword));
    byId("sceneCount").textContent = String(scenes.length);
    byId("sceneList").innerHTML = scenes.length ? scenes.map((scene) => {
      const published = scene.versions.find((version) => version.id === scene.published_version_id);
      return `<button class="entity-list-item ${scene.id === state.selectedSceneId ? "active" : ""}" data-scene-id="${scene.id}" type="button"><small>${escapeHtml(scene.category)} · ${escapeHtml(modeLabel(scene.mode))}</small><strong>${escapeHtml(scene.name)}</strong><div class="entity-list-meta"><span>${escapeHtml(scene.code)}</span><span>${published ? `已发布 ${escapeHtml(published.version)}` : "未发布"}</span></div></button>`;
    }).join("") : '<div class="empty-state"><span>◇</span><strong>暂无检测场景</strong><p>点击右上角创建第一个场景。</p></div>';
  }
  function renderWorkflow(version) {
    const nodes = [...(version.nodes || [])].sort((left, right) => (left.sort_order - right.sort_order) || (left.id - right.id));
    byId("workflowCanvas").innerHTML = nodes.length
      ? nodes.map((node, index) => `<div class="workflow-node workflow-node-${escapeHtml(String(node.node_type || "").toLowerCase())}"><small>${escapeHtml(nodeTypeLabel(node.node_type))}</small><strong>${escapeHtml(node.name)}</strong><small>${escapeHtml(node.node_key)}</small></div>${index < nodes.length - 1 ? '<span class="workflow-arrow" aria-hidden="true">→</span>' : ""}`).join("")
      : '<div class="muted-copy">系统会自动创建开始和结束节点；请添加 VLM 检测、模型检测或规则判断节点。</div>';
  }
  function renderDetail() {
    const scene = selectedScene();
    const version = selectedVersion();
    byId("sceneDetail").hidden = !scene || !version;
    byId("sceneDetailEmpty").hidden = Boolean(scene && version);
    if (!scene || !version) return;
    byId("sceneDetailCode").textContent = scene.code;
    byId("sceneDetailName").textContent = scene.name;
    byId("sceneCategory").value = scene.category;
    byId("sceneMode").value = modeLabel(scene.mode);
    byId("sceneVersionSelect").innerHTML = scene.versions.map((item) => `<option value="${item.id}" ${item.id === version.id ? "selected" : ""}>V${escapeHtml(item.version)} · ${escapeHtml(item.status)}</option>`).join("");
    const status = version.status || "DRAFT";
    byId("sceneVersionStatus").textContent = status;
    byId("sceneVersionStatus").className = `status-pill ${statusClass(status)}`;
    byId("sceneVersionStatusInput").value = status;
    fillVlmSelect("scenePrimaryVlm", version.primary_vlm_model_id);
    fillVlmSelect("sceneReviewVlm", version.review_vlm_model_id);
    fillVlmSelect("workflowVlmModel");
    fillVisionModelSelect();
    byId("scenePrompt").value = version.prompt_template || "";
    const draft = status === "DRAFT";
    const direct = scene.mode === "VLM_DIRECT";
    byId("sceneDirectVlmCard").hidden = !direct;
    byId("sceneWorkflowCard").hidden = direct;
    ["scenePrimaryVlm", "sceneReviewVlm", "scenePrompt", "saveSceneVersion"].forEach((id) => { byId(id).disabled = !draft || !direct; });
    ["addWorkflowNode", "saveWorkflowNode", "workflowNodeType", "workflowVlmModel", "workflowVisionModelVersion"].forEach((id) => { byId(id).disabled = !draft || direct; });
    if (direct) byId("workflowNodeForm").hidden = true;
    byId("publishSceneVersion").hidden = !draft;
    renderWorkflow(version);
  }
  function fillEvaluationOptions() {
    const versions = publishedVersions();
    const optionMarkup = (items, placeholder) => `<option value="">${placeholder}</option>${items.map(({ scene, version }) => `<option value="${version.id}">${escapeHtml(scene.name)} · V${escapeHtml(version.version)}</option>`).join("")}`;
    byId("evaluationSceneVersion").innerHTML = optionMarkup(versions, "请选择已发布场景版本");
    byId("optimizationSceneVersion").innerHTML = optionMarkup(
      versions.filter(({ scene }) => scene.mode === "VLM_DIRECT"),
      "请选择已发布直接 VLM 场景版本",
    );
    const datasetOptions = `<option value="">请选择测试数据集</option>${state.datasets.filter((item) => item.purpose === "TEST" && item.enabled).map((item) => `<option value="${item.id}">${escapeHtml(item.name)} · r${item.revision}</option>`).join("")}`;
    ["evaluationDataset", "optimizationDataset"].forEach((id) => { byId(id).innerHTML = datasetOptions; });
    const vlmOptions = state.vlms.filter((item) => item.enabled).map((item) => `<option value="${item.id}">${escapeHtml(item.name)} · ${escapeHtml(item.model_name)}</option>`).join("");
    byId("optimizationDetectionVlm").innerHTML = `<option value="">请选择检测模型</option>${vlmOptions}`;
    byId("optimizationPromptVlm").innerHTML = `<option value="">请选择优化提示词模型</option>${vlmOptions}`;
  }

  function syncOptimizationModels() {
    const scenarioVersionId = Number(byId("optimizationSceneVersion").value);
    const selected = publishedVersions().find((item) => item.version.id === scenarioVersionId);
    const defaultModelId = selected?.version.primary_vlm_model_id;
    if (defaultModelId) byId("optimizationDetectionVlm").value = String(defaultModelId);
    const promptTemplate = byId("optimizationPromptTemplate");
    promptTemplate.disabled = !selected;
    promptTemplate.value = selected?.version.prompt_template || "";
  }

  function sceneVersionLabel(versionId) {
    const match = state.scenes.flatMap((scene) => scene.versions.map((version) => ({ scene, version }))).find((item) => item.version.id === Number(versionId));
    return match ? `${match.scene.name} · V${match.version.version}` : `场景版本 #${versionId || "—"}`;
  }

  function datasetLabel(datasetId) {
    const dataset = state.datasets.find((item) => item.id === Number(datasetId));
    return dataset ? `${dataset.name} · r${dataset.revision}` : `数据集 #${datasetId || "—"}`;
  }

  function formatDateTime(value) {
    if (!value) return "—";
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleString("zh-CN", { hour12: false });
  }

  function percentage(value) {
    const numeric = Number(value);
    return Number.isFinite(numeric) ? `${(numeric * 100).toFixed(2)}%` : "—";
  }

  function compactText(value, maxLength = 74) { const text = String(value || "").replaceAll(/\s+/g, " ").trim(); return text.length > maxLength ? `${text.slice(0, maxLength)}…` : text || "—"; }
  function vlmLabel(modelId) { const model = state.vlms.find((item) => item.id === Number(modelId)); return model ? model.name : `模型 #${modelId || "—"}`; }
  function taskMetrics(job) { const result = job.result_json || {}; const metrics = result.metrics || result.best_metrics?.metrics || {}; return { accuracy: result.accuracy ?? result.best_metrics?.accuracy ?? metrics.accuracy, total: metrics.total ?? metrics.labeled ?? "—", falseAccept: metrics.false_accept ?? "—", falseReject: metrics.false_reject ?? "—" }; }
  function taskStatusMarkup(job) { return `<span class="status-pill ${statusClass(job.status)}">${escapeHtml(job.status)}</span>`; }
  function taskDetailButton(job) { return `<button class="btn btn-outline-secondary btn-sm" type="button" data-task-detail="${job.id}">详情</button>`; }
  function renderEvaluationTaskTable(jobs) {
    const rows = jobs.map((job) => { const metrics = taskMetrics(job); return `<tr><td>#${job.id}</td><td title="${escapeHtml(sceneVersionLabel(job.scenario_version_id))}">${escapeHtml(compactText(sceneVersionLabel(job.scenario_version_id), 30))}</td><td title="${escapeHtml(datasetLabel(job.dataset_id))}">${escapeHtml(compactText(datasetLabel(job.dataset_id), 24))}</td><td>${percentage(metrics.accuracy)}</td><td>${escapeHtml(metrics.total)}</td><td>${escapeHtml(metrics.falseAccept)}</td><td>${escapeHtml(metrics.falseReject)}</td><td>${taskStatusMarkup(job)}</td><td>${escapeHtml(formatDateTime(job.completed_at || job.created_at))}</td><td>${taskDetailButton(job)}</td></tr>`; }).join("");
    return `<div class="task-table-wrap"><table class="task-table"><thead><tr><th>任务</th><th>场景</th><th>测试数据集</th><th>准确率</th><th>样本</th><th>漏判</th><th>误判</th><th>状态</th><th>完成时间</th><th>操作</th></tr></thead><tbody>${rows}</tbody></table></div>`;
  }
  function renderOptimizationTaskTable(jobs) {
    const rows = jobs.map((job) => { const result = job.result_json || {}; const config = job.config_json || {}; const metrics = taskMetrics(job); const optimizedPrompt = result.best_prompt || "未生成优化提示词"; const detectionModel = config.detection_vlm_model_id ? vlmLabel(config.detection_vlm_model_id) : "场景默认"; const target = result.target_reached === true ? "已达标" : result.target_reached === false ? "未达标" : job.status === "COMPLETED" ? "历史任务" : job.status === "FAILED" ? "失败" : "运行中"; return `<tr><td>#${job.id}</td><td title="${escapeHtml(sceneVersionLabel(job.scenario_version_id))}">${escapeHtml(compactText(sceneVersionLabel(job.scenario_version_id), 28))}</td><td title="${escapeHtml(datasetLabel(job.dataset_id))}">${escapeHtml(compactText(datasetLabel(job.dataset_id), 20))}</td><td title="${escapeHtml(detectionModel)}">${escapeHtml(compactText(detectionModel, 18))}</td><td>${percentage(metrics.accuracy)}</td><td>${percentage(config.target_accuracy)}</td><td>${escapeHtml(target)}</td><td class="task-prompt-cell" title="${escapeHtml(optimizedPrompt)}"><span class="task-prompt-preview">${escapeHtml(compactText(optimizedPrompt, 92))}</span></td><td>${taskStatusMarkup(job)}</td><td>${taskDetailButton(job)}</td></tr>`; }).join("");
    return `<div class="task-table-wrap"><table class="task-table task-table-optimization"><thead><tr><th>任务</th><th>场景</th><th>测试数据集</th><th>检测模型</th><th>最佳准确率</th><th>目标</th><th>结果</th><th>优化后检测提示词</th><th>状态</th><th>操作</th></tr></thead><tbody>${rows}</tbody></table></div>`;
  }
  function renderJobs() {
    const evaluationJobs = state.jobs.filter((item) => item.job_type === "SCENE_EVALUATION");
    const optimizationJobs = state.jobs.filter((item) => item.job_type === "PROMPT_OPTIMIZATION");
    byId("evaluationTaskList").innerHTML = evaluationJobs.length ? renderEvaluationTaskTable(evaluationJobs) : '<div class="empty-task-state"><strong>还没有场景评测任务</strong><p>点击右上角“创建评测任务”，选择已发布场景和测试数据集。</p></div>';
    byId("optimizationTaskList").innerHTML = optimizationJobs.length ? renderOptimizationTaskTable(optimizationJobs) : '<div class="empty-task-state"><strong>还没有场景优化任务</strong><p>点击右上角“创建优化任务”，用测试数据集生成候选提示词。</p></div>';
  }
  function openTaskDetails(jobId) {
    const job = state.jobs.find((item) => item.id === Number(jobId));
    if (!job) return;
    const result = job.result_json || {};
    const config = job.config_json || {};
    const isOptimization = job.job_type === "PROMPT_OPTIMIZATION";
    byId("taskDetailEyebrow").textContent = isOptimization ? "PROMPT OPTIMIZATION" : "SCENE EVALUATION";
    byId("taskDetailTitle").textContent = `${isOptimization ? "场景优化" : "场景评测"}任务 #${job.id}`;
    byId("taskDetailMeta").innerHTML = [["场景", sceneVersionLabel(job.scenario_version_id)], ["测试数据集", datasetLabel(job.dataset_id)], ["状态", job.status], ["创建时间", formatDateTime(job.created_at)], ["完成时间", formatDateTime(job.completed_at)], ["检测模型", isOptimization ? (config.detection_vlm_model_id ? vlmLabel(config.detection_vlm_model_id) : "场景默认") : "按场景配置执行"]].map(([label, value]) => `<div><small>${escapeHtml(label)}</small><strong title="${escapeHtml(value)}">${escapeHtml(value)}</strong></div>`).join("");
    const optimizedPrompt = isOptimization ? `<section class="task-result-section"><div class="section-heading"><div><small>OPTIMIZED PROMPT</small><strong>优化后检测提示词</strong></div><span class="status-pill ${result.target_reached ? "published" : "draft"}">${result.target_reached ? "已达标" : "待确认"}</span></div><pre class="optimized-prompt-output">${escapeHtml(result.best_prompt || "任务尚未生成优化后的提示词。")}</pre>${result.stop_reason ? `<p class="muted-copy mb-0">停止原因：${escapeHtml(result.stop_reason)}</p>` : ""}</section>` : "";
    const error = job.error_message ? `<div class="task-error">${escapeHtml(job.error_message)}</div>` : "";
    const diagnostic = { task_config: config, result, input_snapshot: job.input_snapshot_json || {} };
    byId("taskDetailContent").innerHTML = `${optimizedPrompt}${error}<details class="task-details" open><summary>查看任务参数、每轮结果与完整数据</summary><pre class="job-result">${escapeHtml(JSON.stringify(diagnostic, null, 2))}</pre></details>`;
    openModal("taskDetailModal");
  }
  async function loadData(keepSelection = true) {
    const [scenes, vlms, visionVersions, datasets, jobs] = await Promise.all([request(`${api}/scenarios`), request(`${api}/vlm-models`), request(`${api}/vision-models/published`), request(`${api}/datasets`), request(`${api}/automation-jobs`)]);
    state.scenes = scenes; state.vlms = vlms; state.visionVersions = visionVersions; state.datasets = datasets; state.jobs = jobs;
    if (!keepSelection || !state.scenes.some((item) => item.id === state.selectedSceneId)) {
      state.selectedSceneId = state.scenes[0]?.id ?? null;
      state.selectedVersionId = state.scenes[0]?.versions[0]?.id ?? null;
    }
    renderSceneList(); renderDetail(); fillEvaluationOptions(); renderJobs();
  }
  async function selectScene(id) {
    state.selectedSceneId = Number(id);
    const scene = selectedScene();
    state.selectedVersionId = scene?.versions[0]?.id ?? null;
    renderSceneList(); renderDetail();
  }
  function openModal(id) { new bootstrap.Modal(byId(id)).show(); }
  function closeModal(id) { bootstrap.Modal.getOrCreateInstance(byId(id)).hide(); }
  function nextVersion(value) { const match = String(value || "1.0").match(/^(\d+)(?:\.(\d+))?$/); return match ? `${match[1]}.${Number(match[2] || 0) + 1}` : `${value || "1.0"}-next`; }
  async function createScene() {
    const form = new FormData(byId("sceneCreateForm"));
    const values = Object.fromEntries(form.entries());
    const payload = {
      name: String(values.name || "").trim(),
      category: String(values.category || "").trim() || "GENERAL",
      mode: String(values.mode || "VLM_DIRECT").trim(),
      description: String(values.description || "").trim() || null,
      version: String(values.version || "").trim() || "1.0",
    };
    if (!payload.name) {
      byId("sceneCreateForm").elements.name.focus();
      notify("请先填写场景名称。", "warning");
      return;
    }
    if (payload.mode === "VLM_DIRECT") {
      payload.primary_vlm_model_id = Number(values.primary_vlm_model_id) || null;
      payload.review_vlm_model_id = Number(values.review_vlm_model_id) || null;
      payload.prompt_template = String(values.prompt_template || "").trim() || null;
      if (!payload.primary_vlm_model_id) {
        notify("请先选择主 VLM 模型。", "warning");
        return;
      }
      if (!payload.prompt_template) {
        byId("sceneCreatePrompt").focus();
        notify("请填写检测提示词。", "warning");
        return;
      }
    }
    try {
      const result = await request(`${api}/scenarios`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      closeModal("sceneCreateModal");
      byId("sceneCreateForm").reset();
      await loadData(false);
      await selectScene(state.scenes.find((item) => item.id === result.id)?.id || result.id);
      notify(payload.mode === "WORKFLOW" ? "流程检测场景已创建，开始和结束节点已自动生成。" : "VLM 检测场景与草稿版本已创建。");
    } catch (error) { notify(error.message, "danger"); }
  }
  async function saveVersion() {
    const version = selectedVersion(); if (!version) return;
    const payload = { prompt_template: byId("scenePrompt").value.trim() || null, primary_vlm_model_id: Number(byId("scenePrimaryVlm").value) || null, review_vlm_model_id: Number(byId("sceneReviewVlm").value) || null };
    try { await request(`${api}/scenarios/versions/${version.id}`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) }); await loadData(); notify("场景草稿已保存。"); } catch (error) { notify(error.message, "danger"); }
  }
  async function publishVersion() { const version = selectedVersion(); if (!version) return; if (!confirm("发布后当前草稿将用于 ROI 关联，其他已发布版本会归档。确认发布吗？")) return; try { await request(`${api}/scenarios/versions/${version.id}/publish`, { method: "POST" }); await loadData(); notify("场景版本已发布，可在 ROI 中引用。"); } catch (error) { notify(error.message, "danger"); } }
  async function cloneVersion() { const scene = selectedScene(); const current = selectedVersion(); if (!scene || !current) return; const version = prompt("新草稿版本号", nextVersion(current.version)); if (!version) return; try { const created = await request(`${api}/scenarios/${scene.id}/versions`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ version, source_version_id: current.id, prompt_template: current.prompt_template, primary_vlm_model_id: current.primary_vlm_model_id, review_vlm_model_id: current.review_vlm_model_id, input_schema_json: current.input_schema_json || {}, output_schema_json: current.output_schema_json || {}, definition_json: current.definition_json || {} }) }); await loadData(); state.selectedVersionId = created.id; renderDetail(); notify(scene.mode === "WORKFLOW" ? "已复制当前流程并创建新的草稿版本。" : "已基于当前版本创建新的草稿。"); } catch (error) { notify(error.message, "danger"); } }
  async function addNode() { const version = selectedVersion(); if (!version) return; let config = {}; try { const raw = byId("workflowNodeConfig").value.trim(); config = raw ? JSON.parse(raw) : {}; } catch { notify("节点参数必须是有效 JSON。", "warning"); return; } const nodeType = byId("workflowNodeType").value; if (nodeType === "VLM") { const vlmModelId = Number(byId("workflowVlmModel").value); if (!vlmModelId) { notify("请选择 VLM 模型。", "warning"); return; } config.vlm_model_id = vlmModelId; } if (nodeType === "VISION_MODEL") { const modelVersionId = Number(byId("workflowVisionModelVersion").value); if (!modelVersionId) { notify("请选择可用的已发布训练模型版本。", "warning"); return; } config.model_version_id = modelVersionId; } const payload = { node_key: byId("workflowNodeKey").value.trim(), name: byId("workflowNodeName").value.trim(), node_type: nodeType, config_json: config, sort_order: (version.nodes?.length || 0) + 1 }; if (!payload.node_key || !payload.name) { notify("请填写节点键和节点名称。", "warning"); return; } try { await request(`${api}/scenarios/versions/${version.id}/nodes`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) }); byId("workflowNodeForm").hidden = true; ["workflowNodeKey", "workflowNodeName", "workflowNodeConfig"].forEach((id) => { byId(id).value = ""; }); byId("workflowVlmModel").value = ""; byId("workflowVisionModelVersion").value = ""; await loadData(); notify("流程节点已添加。"); } catch (error) { notify(error.message, "danger"); } }
  async function runTest() { const version = selectedVersion(); const imagePath = byId("sceneTestImagePath").value.trim(); if (!version || !imagePath) { notify("请选择场景版本并填写平台可访问的图片路径。", "warning"); return; } byId("sceneTestOutput").textContent = "正在执行…"; try { const result = await request(`${api}/scenarios/versions/${version.id}/execute`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ image_path: imagePath, enqueue_review: byId("sceneTestReview").checked }) }); byId("sceneTestOutput").textContent = JSON.stringify(result, null, 2); if (byId("sceneTestReview").checked) setTimeout(() => loadData(), 500); } catch (error) { byId("sceneTestOutput").textContent = error.message; notify(error.message, "danger"); } }
  function openEvaluationTask() { fillEvaluationOptions(); openModal("evaluationTaskModal"); }
  function openOptimizationTask() { fillEvaluationOptions(); byId("optimizationRequirements").value = ""; syncOptimizationModels(); openModal("optimizationTaskModal"); }
  async function queueEvaluation() { const scenarioVersionId = Number(byId("evaluationSceneVersion").value); const datasetId = Number(byId("evaluationDataset").value); if (!scenarioVersionId || !datasetId) { notify("请选择已发布场景和测试数据集。", "warning"); return; } try { const result = await request(`${api}/automation-jobs/scene-evaluations`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ scenario_version_id: scenarioVersionId, dataset_id: datasetId }) }); closeModal("evaluationTaskModal"); notify(`评测任务 #${result.job_id} 已进入队列。`); await loadData(); } catch (error) { notify(error.message, "danger"); } }
  async function queueOptimization() { const scenarioVersionId = Number(byId("optimizationSceneVersion").value); const datasetId = Number(byId("optimizationDataset").value); const detectionVlmModelId = Number(byId("optimizationDetectionVlm").value); const optimizerVlmModelId = Number(byId("optimizationPromptVlm").value); const promptTemplate = byId("optimizationPromptTemplate").value.trim(); const optimizationRequirements = byId("optimizationRequirements").value.trim(); if (!scenarioVersionId || !datasetId || !detectionVlmModelId || !optimizerVlmModelId || !promptTemplate || !optimizationRequirements) { notify("请选择场景、测试数据集、检测模型和优化提示词模型，并填写待优化提示词与优化要求。", "warning"); return; } try { const result = await request(`${api}/automation-jobs/prompt-optimizations`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ scenario_version_id: scenarioVersionId, dataset_id: datasetId, detection_vlm_model_id: detectionVlmModelId, optimizer_vlm_model_id: optimizerVlmModelId, prompt_template: promptTemplate, optimization_requirements: optimizationRequirements, target_accuracy: Number(byId("optimizationTargetAccuracy").value), max_rounds: Number(byId("optimizationMaxRounds").value) }) }); closeModal("optimizationTaskModal"); notify(result.message || "优化任务已创建。", "info"); await loadData(); } catch (error) { notify(error.message, "danger"); } }
  document.addEventListener("DOMContentLoaded", () => {
    byId("openSceneCreate").addEventListener("click", openSceneCreate); byId("sceneCreateMode").addEventListener("change", syncSceneCreateMode); byId("createScene").addEventListener("click", createScene); byId("reloadScenes").addEventListener("click", () => loadData()); byId("sceneSearch").addEventListener("input", renderSceneList); byId("sceneList").addEventListener("click", (event) => { const button = event.target.closest("[data-scene-id]"); if (button) selectScene(button.dataset.sceneId); });
    byId("sceneVersionSelect").addEventListener("change", (event) => { state.selectedVersionId = Number(event.target.value); renderDetail(); }); byId("saveSceneVersion").addEventListener("click", saveVersion); byId("publishSceneVersion").addEventListener("click", publishVersion); byId("cloneSceneVersion").addEventListener("click", cloneVersion); byId("addWorkflowNode").addEventListener("click", () => { byId("workflowNodeForm").hidden = !byId("workflowNodeForm").hidden; syncWorkflowNodeForm(); }); byId("workflowNodeType").addEventListener("change", syncWorkflowNodeForm); byId("saveWorkflowNode").addEventListener("click", addNode); ["runSceneTest", "runSceneTestBottom"].forEach((id) => byId(id).addEventListener("click", runTest));
    document.querySelectorAll("[data-scene-view]").forEach((button) => button.addEventListener("click", () => { document.querySelectorAll("[data-scene-view]").forEach((item) => item.classList.toggle("active", item === button)); document.querySelectorAll(".management-view").forEach((item) => item.classList.toggle("active", item.id === `${button.dataset.sceneView}View`)); })); byId("openEvaluationTask").addEventListener("click", openEvaluationTask); byId("openOptimizationTask").addEventListener("click", openOptimizationTask); byId("queueEvaluation").addEventListener("click", queueEvaluation); byId("queueOptimization").addEventListener("click", queueOptimization); byId("optimizationSceneVersion").addEventListener("change", syncOptimizationModels); ["reloadEvaluationJobs", "reloadOptimizationJobs"].forEach((id) => byId(id).addEventListener("click", () => loadData())); document.addEventListener("click", (event) => { const button = event.target.closest("[data-task-detail]"); if (button) openTaskDetails(button.dataset.taskDetail); });
    loadData(false).catch((error) => notify(error.message, "danger"));
  });
})();
