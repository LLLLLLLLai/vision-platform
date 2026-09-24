(() => {
  const api = "/api/v1";
  const state = {
    scenes: [],
    vlms: [],
    datasets: [],
    jobs: [],
    promptValues: new Map(),
    activePromptKey: null,
    nextPromptKey: 0,
  };
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
    if (!response.ok) {
      const detail = Array.isArray(payload.detail)
        ? payload.detail.map((item) => `${item.loc?.at?.(-1) || "参数"}：${item.msg || "格式不正确"}`).join("；")
        : payload.detail || payload.message || `请求失败（${response.status}）`;
      throw new Error(detail);
    }
    return payload;
  };
  const notify = (message, type = "success") => {
    byId("sceneAlert").innerHTML = `<div class="alert alert-${type} alert-dismissible fade show">${escapeHtml(message)}<button class="btn-close" data-bs-dismiss="alert"></button></div>`;
  };
  const openModal = (id) => window.bootstrap.Modal.getOrCreateInstance(byId(id)).show();
  const closeModal = (id) => window.bootstrap.Modal.getOrCreateInstance(byId(id)).hide();
  const modeLabel = (mode) => mode === "WORKFLOW" ? "流程检测" : "VLM 检测";
  const statusClass = (status) => {
    const value = String(status || "DRAFT").toLowerCase();
    return ["published", "completed"].includes(value) ? "published" : ["failed", "canceled"].includes(value) ? "error" : "draft";
  };
  const formatDateTime = (value) => {
    if (!value) return "—";
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleString("zh-CN", { hour12: false });
  };
  const percentage = (value) => Number.isFinite(Number(value)) ? `${(Number(value) * 100).toFixed(2)}%` : "—";
  const compact = (value, length = 56) => {
    const text = String(value || "").replaceAll(/\s+/g, " ").trim();
    return text.length > length ? `${text.slice(0, length)}…` : text || "—";
  };
  const validTargetAccuracy = (result = {}, config = {}) => {
    const target = Number(result.target_accuracy ?? config.target_accuracy);
    return Number.isFinite(target) && target > 0 && target <= 1 ? target : null;
  };
  const metricTargetReached = (metrics = {}, target) => {
    const accuracy = Number(metrics.accuracy);
    const labeled = Number(metrics.labeled ?? metrics.total ?? 0);
    return target !== null
      && Number.isFinite(accuracy)
      && Number.isFinite(labeled)
      && labeled > 0
      && accuracy >= target;
  };
  const optimizerRequirementsClaim = (result = {}) => Boolean(
    result.optimizer_requirements_claim ?? result.requirements_satisfied,
  );
  const optimizationOutcome = (result = {}, config = {}, metrics = {}, jobStatus = "COMPLETED") => {
    const target = validTargetAccuracy(result, config);
    const metricTargetMet = metricTargetReached(metrics, target);
    const requirementsClaimed = optimizerRequirementsClaim(result);
    if (jobStatus === "FAILED") {
      return { label: "失败", className: "error", target, metricTargetMet, requirementsClaimed };
    }
    if (jobStatus !== "COMPLETED") {
      return { label: "运行中", className: "draft", target, metricTargetMet, requirementsClaimed };
    }
    if (target === null) {
      return { label: "目标无效，需重跑", className: "error", target, metricTargetMet: false, requirementsClaimed };
    }
    if (metricTargetMet && requirementsClaimed) {
      return { label: "已完成", className: "published", target, metricTargetMet, requirementsClaimed };
    }
    if (metricTargetMet) {
      return { label: "实测达标，待要求核对", className: "draft", target, metricTargetMet, requirementsClaimed };
    }
    return { label: "实测未达标", className: "draft", target, metricTargetMet, requirementsClaimed };
  };
  const registerPrompt = (value) => {
    const prompt = String(value || "").trim();
    if (!prompt) return "";
    const key = `prompt-${++state.nextPromptKey}`;
    state.promptValues.set(key, prompt);
    return key;
  };
  const promptActionButtons = (prompt, className = "") => {
    const key = registerPrompt(prompt);
    if (!key) return "";
    return `<div class="prompt-action-group ${className}"><button class="btn btn-sm btn-outline-primary" type="button" data-prompt-view="${key}">查看完整</button><button class="btn btn-sm btn-outline-secondary" type="button" data-prompt-copy="${key}">复制</button></div>`;
  };
  const fallbackCopyText = (text) => {
    const textarea = document.createElement("textarea");
    textarea.value = text;
    textarea.setAttribute("readonly", "");
    textarea.style.position = "fixed";
    textarea.style.opacity = "0";
    document.body.append(textarea);
    textarea.select();
    const copied = document.execCommand("copy");
    textarea.remove();
    if (!copied) throw new Error("浏览器未允许复制");
  };
  const copyPrompt = async (key) => {
    const prompt = state.promptValues.get(key);
    if (!prompt) {
      notify("未找到需要复制的提示词，请刷新页面后重试。", "warning");
      return;
    }
    try {
      if (navigator.clipboard?.writeText) {
        await navigator.clipboard.writeText(prompt);
      } else {
        fallbackCopyText(prompt);
      }
      notify("完整提示词已复制。", "success");
    } catch (_error) {
      try {
        fallbackCopyText(prompt);
        notify("完整提示词已复制。", "success");
      } catch (copyError) {
        notify(`复制失败：${copyError.message}`, "danger");
      }
    }
  };
  const openPromptViewer = (key) => {
    const prompt = state.promptValues.get(key);
    if (!prompt) {
      notify("未找到完整提示词，请刷新页面后重试。", "warning");
      return;
    }
    state.activePromptKey = key;
    byId("promptViewerContent").textContent = prompt;
    openModal("promptViewerModal");
  };
  const publishedVersions = () => state.scenes.flatMap((scene) => scene.versions
    .filter((version) => version.status === "PUBLISHED")
    .map((version) => ({ scene, version })));
  const sceneVersionLabel = (versionId) => {
    const match = state.scenes.flatMap((scene) => scene.versions.map((version) => ({ scene, version })))
      .find((item) => item.version.id === Number(versionId));
    return match ? `${match.scene.name} · V${match.version.version}` : `场景版本 #${versionId || "—"}`;
  };
  const datasetLabel = (datasetId) => {
    const dataset = state.datasets.find((item) => item.id === Number(datasetId));
    return dataset ? `${dataset.name} · r${dataset.revision}` : `数据集 #${datasetId || "—"}`;
  };
  const vlmLabel = (modelId) => {
    const model = state.vlms.find((item) => item.id === Number(modelId));
    return model ? model.name : `模型 #${modelId || "—"}`;
  };

  function renderSceneTable() {
    const keyword = byId("sceneSearch").value.trim().toLowerCase();
    const scenes = state.scenes.filter((scene) => !keyword || [scene.code, scene.name, scene.category, modeLabel(scene.mode)]
      .join(" ").toLowerCase().includes(keyword));
    if (!scenes.length) {
      byId("sceneTable").innerHTML = '<div class="empty-task-state"><strong>还没有检测场景</strong><p>点击右上角“新建检测场景”，随后进入专属设计页。</p></div>';
      return;
    }
    const rows = scenes.map((scene) => {
      const current = scene.versions.find((version) => version.id === scene.published_version_id) || scene.versions[0];
      const versionText = current ? `V${current.version}` : "—";
      const status = current?.status || "DRAFT";
      return `<tr>
        <td><strong>${escapeHtml(scene.name)}</strong><small>${escapeHtml(scene.code)}</small></td>
        <td>${escapeHtml(scene.category || "GENERAL")}</td>
        <td>${escapeHtml(modeLabel(scene.mode))}</td>
        <td>${escapeHtml(versionText)}</td>
        <td><span class="status-pill ${statusClass(status)}">${escapeHtml(status)}</span></td>
        <td>${scene.versions.length}</td>
        <td>${escapeHtml(compact(scene.description, 48))}</td>
        <td>${escapeHtml(formatDateTime(scene.updated_at || scene.created_at))}</td>
        <td><a class="btn btn-primary btn-sm" href="/scenes/designer/${scene.id}">进入设计</a></td>
      </tr>`;
    }).join("");
    byId("sceneTable").innerHTML = `<div class="task-table-wrap"><table class="task-table scene-catalog-table"><thead><tr><th>场景</th><th>分类</th><th>类型</th><th>当前版本</th><th>状态</th><th>版本数</th><th>说明</th><th>更新时间</th><th>操作</th></tr></thead><tbody>${rows}</tbody></table></div>`;
  }

  function fillEvaluationOptions() {
    const options = (items, placeholder) => `<option value="">${placeholder}</option>${items.map(({ scene, version }) => `<option value="${version.id}">${escapeHtml(scene.name)} · V${escapeHtml(version.version)}</option>`).join("")}`;
    byId("evaluationSceneVersion").innerHTML = options(publishedVersions(), "请选择已发布场景版本");
    byId("optimizationSceneVersion").innerHTML = options(
      publishedVersions().filter(({ scene }) => scene.mode === "VLM_DIRECT"),
      "请选择已发布直接 VLM 场景版本",
    );
    const datasetOptions = `<option value="">请选择测试数据集</option>${state.datasets
      .filter((item) => item.purpose === "TEST" && item.enabled)
      .map((item) => `<option value="${item.id}">${escapeHtml(item.name)} · r${item.revision}</option>`).join("")}`;
    byId("evaluationDataset").innerHTML = datasetOptions;
    byId("optimizationDataset").innerHTML = datasetOptions;
    const vlmOptions = state.vlms.filter((item) => item.enabled)
      .map((item) => `<option value="${item.id}">${escapeHtml(item.name)} · ${escapeHtml(item.model_name)}</option>`).join("");
    byId("optimizationDetectionVlm").innerHTML = `<option value="">请选择检测模型</option>${vlmOptions}`;
    byId("optimizationPromptVlm").innerHTML = `<option value="">请选择优化提示词模型</option>${vlmOptions}`;
  }

  function syncOptimizationModels() {
    const versionId = Number(byId("optimizationSceneVersion").value);
    const selected = publishedVersions().find((item) => item.version.id === versionId);
    byId("optimizationPromptTemplate").disabled = !selected;
    byId("optimizationPromptTemplate").value = selected?.version.prompt_template || "";
    if (selected?.version.primary_vlm_model_id) byId("optimizationDetectionVlm").value = String(selected.version.primary_vlm_model_id);
    renderOptimizationInputValues(selected);
  }

  function optimizationVariableFields(selected) {
    const fields = selected?.version?.input_schema_json?.fields;
    if (!Array.isArray(fields)) return [];
    return fields.filter((field) => {
      const name = String(field?.name || "").trim();
      return name && !["image", "image_path", "image_url"].includes(name.toLowerCase());
    });
  }

  function renderOptimizationInputValues(selected) {
    const container = byId("optimizationInputValues");
    const fields = optimizationVariableFields(selected);
    if (!fields.length) {
      container.hidden = true;
      container.innerHTML = "";
      return;
    }
    container.hidden = false;
    container.innerHTML = `<div class="optimization-input-heading"><div><small>VARIABLE EXAMPLES</small><strong>评测变量示例</strong></div><span>只用于本次测试集渲染，提示词中的 <code>{{ input.xxx }}</code> 会自动保留。</span></div><div class="optimization-input-grid">${fields.map((field) => {
      const name = String(field.name).trim();
      const label = String(field.label || name).trim();
      const value = field.default ?? "";
      return `<label><span>${escapeHtml(label)}${field.required ? " <em>必填</em>" : ""}</span><input class="form-control form-control-sm" data-optimization-input="${escapeHtml(name)}" value="${escapeHtml(value)}" placeholder="${escapeHtml(name)}"></label>`;
    }).join("")}</div>`;
  }

  function taskMetrics(job) {
    const result = job.result_json || {};
    const metrics = result.metrics || result.best_metrics?.metrics || {};
    return {
      accuracy: result.accuracy ?? result.best_metrics?.accuracy ?? metrics.accuracy,
      total: metrics.total ?? metrics.labeled ?? "—",
      falseAccept: metrics.false_accept ?? "—",
      falseReject: metrics.false_reject ?? "—",
    };
  }
  const taskStatus = (job) => {
    const labels = { CANCEL_REQUESTED: "停止中", WAITING_GPU: "等待 GPU", QUEUED: "排队中" };
    return `<span class="status-pill ${statusClass(job.status)}">${escapeHtml(labels[job.status] || job.status)}</span>`;
  };
  const taskButton = (job) => {
    const stopping = job.status === "CANCEL_REQUESTED";
    const stop = (job.can_stop || stopping)
      ? `<button class="btn btn-outline-danger btn-sm" type="button" data-job-stop="${job.id}" ${stopping ? "disabled" : ""}>${stopping ? "停止中" : "停止"}</button>`
      : "";
    const restart = job.can_restart
      ? `<button class="btn btn-outline-primary btn-sm" type="button" data-job-restart="${job.id}">重新启动</button>`
      : "";
    return `<div class="table-action-group"><button class="btn btn-outline-secondary btn-sm" type="button" data-job-detail="${job.id}">详情</button>${stop}${restart}</div>`;
  };

  function renderEvaluationTasks(jobs) {
    if (!jobs.length) return '<div class="empty-task-state"><strong>还没有场景评测任务</strong><p>点击右上角“创建评测任务”，选择已发布场景和测试数据集。</p></div>';
    const rows = jobs.map((job) => {
      const metrics = taskMetrics(job);
      return `<tr><td>#${job.id}</td><td>${escapeHtml(compact(sceneVersionLabel(job.scenario_version_id), 32))}</td><td>${escapeHtml(compact(datasetLabel(job.dataset_id), 28))}</td><td>${percentage(metrics.accuracy)}</td><td>${escapeHtml(metrics.total)}</td><td>${escapeHtml(metrics.falseAccept)}</td><td>${escapeHtml(metrics.falseReject)}</td><td>${taskStatus(job)}</td><td>${escapeHtml(formatDateTime(job.completed_at || job.created_at))}</td><td>${taskButton(job)}</td></tr>`;
    }).join("");
    return `<div class="task-table-wrap"><table class="task-table"><thead><tr><th>任务</th><th>场景</th><th>测试数据集</th><th>准确率</th><th>样本</th><th>漏判</th><th>误判</th><th>状态</th><th>完成时间</th><th>操作</th></tr></thead><tbody>${rows}</tbody></table></div>`;
  }

  function renderOptimizationTasks(jobs) {
    if (!jobs.length) return '<div class="empty-task-state"><strong>还没有场景优化任务</strong><p>点击右上角“创建优化任务”，用测试数据集生成候选提示词。</p></div>';
    const rows = jobs.map((job) => {
      const result = job.result_json || {};
      const config = job.config_json || {};
      const metrics = taskMetrics(job);
      const prompt = result.best_prompt || "未生成优化提示词";
      const outcome = optimizationOutcome(result, config, metrics, job.status);
      const target = outcome.target === null ? "无效" : percentage(outcome.target);
      return `<tr><td>#${job.id}</td><td>${escapeHtml(compact(sceneVersionLabel(job.scenario_version_id), 28))}</td><td>${escapeHtml(compact(datasetLabel(job.dataset_id), 22))}</td><td>${escapeHtml(compact(config.detection_vlm_model_id ? vlmLabel(config.detection_vlm_model_id) : "场景默认", 20))}</td><td>${percentage(metrics.accuracy)}</td><td>${escapeHtml(target)}</td><td><span class="status-pill ${outcome.className}">${escapeHtml(outcome.label)}</span></td><td class="task-prompt-cell"><div class="task-prompt-cell-content"><span class="task-prompt-preview">${escapeHtml(compact(prompt, 92))}</span>${promptActionButtons(prompt, "prompt-action-group-inline")}</div></td><td>${taskStatus(job)}</td><td>${taskButton(job)}</td></tr>`;
    }).join("");
    return `<div class="task-table-wrap"><table class="task-table task-table-optimization"><thead><tr><th>任务</th><th>场景</th><th>测试数据集</th><th>检测模型</th><th>最佳准确率</th><th>目标</th><th>结果</th><th>优化后检测提示词</th><th>状态</th><th>操作</th></tr></thead><tbody>${rows}</tbody></table></div>`;
  }

  function renderJobs() {
    state.promptValues.clear();
    state.activePromptKey = null;
    state.nextPromptKey = 0;
    byId("evaluationTaskList").innerHTML = renderEvaluationTasks(state.jobs.filter((job) => job.job_type === "SCENE_EVALUATION"));
    byId("optimizationTaskList").innerHTML = renderOptimizationTasks(state.jobs.filter((job) => job.job_type === "PROMPT_OPTIMIZATION"));
  }

  function taskMetricCards(metrics, cards) {
    return `<div class="task-metric-grid">${cards.map(([label, value]) => `<div><small>${escapeHtml(label)}</small><strong>${escapeHtml(value ?? "—")}</strong></div>`).join("")}</div>`;
  }

  function evaluationTaskDetails(result) {
    const metrics = result.metrics || {};
    const items = Array.isArray(result.items) ? result.items : [];
    const cards = taskMetricCards(metrics, [
      ["准确率", percentage(metrics.accuracy)],
      ["已标注样本", metrics.labeled ?? "—"],
      ["正确", metrics.correct ?? "—"],
      ["漏判（NG → OK）", metrics.false_accept ?? "—"],
      ["误判（OK → NG）", metrics.false_reject ?? "—"],
      ["不确定或异常", metrics.uncertain_or_error ?? "—"],
    ]);
    const itemRows = items.length
      ? items.map((item) => {
        const expected = item.ground_truth || "未标注";
        const actual = item.result || "—";
        const matched = expected === "未标注" ? "—" : expected === actual ? "正确" : "不一致";
        return `<tr><td>#${escapeHtml(item.dataset_item_id)}</td><td>${escapeHtml(expected)}</td><td>${escapeHtml(actual)}</td><td>${escapeHtml(matched)}</td><td>${escapeHtml(item.elapsed_ms === undefined || item.elapsed_ms === null ? "—" : `${Number(item.elapsed_ms).toFixed(0)} ms`)}</td><td>#${escapeHtml(item.execution_id || "—")}</td></tr>`;
      }).join("")
      : '<tr><td colspan="6" class="text-center text-muted">任务尚未产出逐样本评测结果。</td></tr>';
    return `<section class="task-result-section"><div class="section-heading"><div><small>EVALUATION METRICS</small><strong>评测指标</strong></div></div>${cards}</section><section class="task-result-section"><div class="section-heading"><div><small>SAMPLE RESULTS</small><strong>逐样本结果</strong></div></div><div class="task-table-wrap"><table class="task-table task-detail-table"><thead><tr><th>数据集样本</th><th>人工标注</th><th>场景结果</th><th>对比</th><th>耗时</th><th>执行记录</th></tr></thead><tbody>${itemRows}</tbody></table></div></section>`;
  }

  function optimizationTaskDetails(result, config, jobStatus) {
    const bestMetrics = result.best_metrics?.metrics || result.metrics || {};
    const history = Array.isArray(result.history) ? result.history : [];
    const outcome = optimizationOutcome(result, config, bestMetrics, jobStatus);
    const bestPrompt = result.best_prompt || "";
    const bestPromptKey = registerPrompt(bestPrompt);
    const cards = taskMetricCards(bestMetrics, [
      ["最佳准确率", percentage(result.best_metrics?.accuracy ?? bestMetrics.accuracy)],
      ["目标准确率", outcome.target === null ? "无效" : percentage(outcome.target)],
      ["优化轮数", history.length ? Math.max(0, history.length - 1) : "—"],
      ["实测目标", outcome.target === null ? "目标无效，需重跑" : outcome.metricTargetMet ? "已达到" : "未达到"],
      ["优化要求核对", outcome.requirementsClaimed ? "优化模型声明已满足" : "优化模型未声明满足"],
      ["总体结果", outcome.label],
      ["最佳漏判", bestMetrics.false_accept ?? "—"],
    ]);
    const rounds = history.length
      ? history.map((round) => {
        const metrics = round.metrics || {};
        const roundPrompt = String(round.prompt || "").trim();
        const requirementsClaimed = optimizerRequirementsClaim(round);
        const roundMetricTargetMet = metricTargetReached(metrics, outcome.target);
        const roundCompleted = roundMetricTargetMet && requirementsClaimed;
        const roundStatus = round.status || "已评测";
        const roundResult = outcome.target === null
          ? "目标无效"
          : roundCompleted
            ? "已完成"
            : roundMetricTargetMet
              ? "实测达标，待核对"
              : "实测未达标";
        const roundClassName = outcome.target === null
          ? "error"
          : roundCompleted
            ? "published"
            : roundStatus === "SKIPPED"
              ? "error"
              : "draft";
        const reason = round.requirement_reason || round.reason || "—";
        const attempts = round.optimizer_attempts?.attempt_count
          ? `优化模型 ${round.optimizer_attempts.attempt_count} 次`
          : round.retry_count !== undefined
            ? `检测重试 ${round.retry_count} 次`
            : "—";
        return `<tr><td>${Number(round.round) === 0 ? "基线" : `第 ${escapeHtml(round.round ?? "—")} 轮`}</td><td><span class="status-pill ${roundClassName}">${escapeHtml(roundResult)}</span><small>${escapeHtml(roundStatus)}</small></td><td>${percentage(round.accuracy ?? metrics.accuracy)}</td><td>${escapeHtml(metrics.labeled ?? metrics.total ?? "—")}</td><td>${escapeHtml(metrics.false_accept ?? "—")}</td><td>${escapeHtml(metrics.false_reject ?? "—")}</td><td>${escapeHtml(attempts)}</td><td class="optimization-round-reason" title="${escapeHtml(reason)}">${escapeHtml(compact(reason, 90))}</td><td class="task-prompt-cell"><div class="task-prompt-cell-content"><span class="task-prompt-preview">${escapeHtml(compact(roundPrompt || reason, 130))}</span>${roundPrompt ? promptActionButtons(roundPrompt, "prompt-action-group-inline") : ""}</div></td></tr>`;
      }).join("")
      : '<tr><td colspan="9" class="text-center text-muted">任务尚未产出优化轮次结果。</td></tr>';
    const copyButton = bestPromptKey
      ? `<button class="btn btn-sm btn-outline-light prompt-copy-float" type="button" data-prompt-copy="${bestPromptKey}">⧉ 复制提示词</button>`
      : "";
    return `<section class="task-result-section"><div class="section-heading"><div><small>OPTIMIZATION RESULT</small><strong>优化结果</strong></div><span class="status-pill ${outcome.className}">${escapeHtml(outcome.label)}</span></div>${cards}<p class="task-description-copy">停止原因：${escapeHtml(result.stop_reason || "任务尚未结束。")}</p><p class="muted-copy mb-0">达标规则：必须有已标注测试样本，实测准确率达到大于 0% 的目标值；同时由优化模型核对用户的优化要求。两项结果单独展示，避免将模型说明当作实际准确率。</p></section><section class="task-result-section prompt-result-section"><div class="section-heading prompt-section-heading"><div><small>OPTIMIZED PROMPT</small><strong>优化后检测提示词</strong></div></div>${copyButton}<pre class="optimized-prompt-output">${escapeHtml(bestPrompt || "任务尚未生成优化后的提示词。")}</pre></section><section class="task-result-section"><div class="section-heading"><div><small>OPTIMIZATION REQUIREMENT</small><strong>用户优化要求</strong></div></div><p class="task-description-copy">${escapeHtml(result.optimization_requirements || config.optimization_requirements || "—")}</p><p class="muted-copy mb-0">优化模型最终说明：${escapeHtml(result.requirement_reason || "尚未生成说明。")}</p></section><section class="task-result-section"><div class="section-heading"><div><small>ROUND HISTORY</small><strong>每轮优化结果</strong></div></div><div class="prompt-round-list task-table-wrap"><table class="task-table optimization-round-table"><thead><tr><th>轮次</th><th>状态</th><th>准确率</th><th>已标注</th><th>漏判</th><th>误判</th><th>调用/重试</th><th>优化说明</th><th>候选提示词</th></tr></thead><tbody>${rounds}</tbody></table></div></section>`;
  }

  function openTaskDetails(jobId) {
    const job = state.jobs.find((item) => item.id === Number(jobId));
    if (!job) return;
    const result = job.result_json || {};
    const config = job.config_json || {};
    const isOptimization = job.job_type === "PROMPT_OPTIMIZATION";
    byId("taskDetailEyebrow").textContent = isOptimization ? "PROMPT OPTIMIZATION" : "SCENE EVALUATION";
    byId("taskDetailTitle").textContent = `${isOptimization ? "场景优化" : "场景评测"}任务 #${job.id}`;
    byId("taskDetailMeta").innerHTML = [
      ["场景", sceneVersionLabel(job.scenario_version_id)],
      ["测试数据集", datasetLabel(job.dataset_id)],
      ["状态", job.status],
      ["创建时间", formatDateTime(job.created_at)],
      ["完成时间", formatDateTime(job.completed_at)],
      ["检测模型", isOptimization && config.detection_vlm_model_id ? vlmLabel(config.detection_vlm_model_id) : "按场景配置执行"],
    ].map(([label, value]) => `<div><small>${escapeHtml(label)}</small><strong title="${escapeHtml(value)}">${escapeHtml(value)}</strong></div>`).join("");
    const error = job.error_message ? `<div class="task-error">${escapeHtml(job.error_message)}</div>` : "";
    const diagnostic = { task_config: config, result, input_snapshot: job.input_snapshot_json || {} };
    const detail = isOptimization ? optimizationTaskDetails(result, config, job.status) : evaluationTaskDetails(result);
    byId("taskDetailContent").innerHTML = `${error}${detail}<details class="task-details"><summary>查看完整任务参数与原始结果</summary><pre class="job-result">${escapeHtml(JSON.stringify(diagnostic, null, 2))}</pre></details>`;
    openModal("taskDetailModal");
  }

  async function createScene() {
    const form = byId("sceneCreateForm");
    const values = Object.fromEntries(new FormData(form).entries());
    if (!values.name?.trim()) {
      notify("请填写场景名称。", "warning");
      return;
    }
    const payload = {
      name: values.name.trim(),
      category: values.category.trim() || "GENERAL",
      mode: values.mode,
      description: values.description.trim() || null,
      version: values.version.trim() || "1.0",
    };
    try {
      const created = await request(`${api}/scenarios`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      window.location.assign(`/scenes/designer/${created.id}`);
    } catch (error) {
      notify(error.message, "danger");
    }
  }

  async function queueEvaluation() {
    const scenarioVersionId = Number(byId("evaluationSceneVersion").value);
    const datasetId = Number(byId("evaluationDataset").value);
    if (!scenarioVersionId || !datasetId) {
      notify("请选择已发布场景和测试数据集。", "warning");
      return;
    }
    try {
      const result = await request(`${api}/automation-jobs/scene-evaluations`, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ scenario_version_id: scenarioVersionId, dataset_id: datasetId }),
      });
      closeModal("evaluationTaskModal");
      notify(`评测任务 #${result.job_id} 已进入评测任务列表。`);
      await loadData();
    } catch (error) { notify(error.message, "danger"); }
  }

  async function queueOptimization() {
    const targetAccuracy = Number(byId("optimizationTargetAccuracy").value);
    const inputValues = {};
    document.querySelectorAll("[data-optimization-input]").forEach((input) => {
      const name = String(input.dataset.optimizationInput || "").trim();
      if (name) inputValues[name] = input.value;
    });
    const payload = {
      scenario_version_id: Number(byId("optimizationSceneVersion").value),
      dataset_id: Number(byId("optimizationDataset").value),
      detection_vlm_model_id: Number(byId("optimizationDetectionVlm").value),
      optimizer_vlm_model_id: Number(byId("optimizationPromptVlm").value),
      prompt_template: byId("optimizationPromptTemplate").value.trim(),
      optimization_requirements: byId("optimizationRequirements").value.trim(),
      target_accuracy: targetAccuracy,
      max_rounds: Number(byId("optimizationMaxRounds").value),
      input_values: inputValues,
    };
    if (
      !payload.scenario_version_id
      || !payload.dataset_id
      || !payload.detection_vlm_model_id
      || !payload.optimizer_vlm_model_id
      || !payload.prompt_template
      || !payload.optimization_requirements
      || !Number.isFinite(targetAccuracy)
      || targetAccuracy <= 0
      || targetAccuracy > 1
      || !Number.isInteger(payload.max_rounds)
      || payload.max_rounds < 1
    ) {
      notify("请完整填写任务信息；目标准确率必须大于 0% 且不超过 100%。", "warning");
      return;
    }
    try {
      const result = await request(`${api}/automation-jobs/prompt-optimizations`, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload),
      });
      closeModal("optimizationTaskModal");
      notify(result.message || "优化任务已创建。");
      await loadData();
    } catch (error) { notify(error.message, "danger"); }
  }

  async function loadData() {
    const [scenes, vlms, datasets, jobs] = await Promise.all([
      request(`${api}/scenarios`), request(`${api}/vlm-models`), request(`${api}/datasets`), request(`${api}/automation-jobs`),
    ]);
    state.scenes = scenes;
    state.vlms = vlms;
    state.datasets = datasets;
    state.jobs = jobs;
    renderSceneTable();
    renderJobs();
    const taskId = Number(new URLSearchParams(window.location.search).get("task_id"));
    if (taskId && state.jobs.some((job) => job.id === taskId)) {
      window.history.replaceState({}, "", window.location.pathname);
      openTaskDetails(taskId);
    }
  }

  async function stopTask(jobId) {
    const job = state.jobs.find((item) => item.id === Number(jobId));
    if (!job) return;
    try {
      const result = await request(`${api}/automation-jobs/${job.id}/cancel`, { method: "POST" });
      notify(result.message || "已请求停止任务。");
      await loadData();
    } catch (error) { notify(error.message, "danger"); }
  }

  async function restartTask(jobId) {
    const job = state.jobs.find((item) => item.id === Number(jobId));
    if (!job) return;
    if (!confirm(`重新启动任务 #${job.id} 吗？将沿用该任务原有的场景、数据集和参数。`)) return;
    try {
      const result = await request(`${api}/automation-jobs/${job.id}/restart`, { method: "POST" });
      notify(result.message || "任务已重新进入队列。");
      await loadData();
    } catch (error) { notify(error.message, "danger"); }
  }

  document.addEventListener("DOMContentLoaded", () => {
    byId("reloadScenes").addEventListener("click", () => loadData().catch((error) => notify(error.message, "danger")));
    byId("sceneSearch").addEventListener("input", renderSceneTable);
    byId("openSceneCreate")?.addEventListener("click", () => { byId("sceneCreateForm").reset(); byId("sceneCreateForm").elements.category.value = "HARNESS"; byId("sceneCreateForm").elements.version.value = "1.0"; openModal("sceneCreateModal"); });
    byId("createScene").addEventListener("click", createScene);
    byId("openEvaluationTask").addEventListener("click", () => { fillEvaluationOptions(); openModal("evaluationTaskModal"); });
    byId("openOptimizationTask").addEventListener("click", () => { fillEvaluationOptions(); byId("optimizationRequirements").value = ""; syncOptimizationModels(); openModal("optimizationTaskModal"); });
    byId("queueEvaluation").addEventListener("click", queueEvaluation);
    byId("queueOptimization").addEventListener("click", queueOptimization);
    byId("optimizationSceneVersion").addEventListener("change", syncOptimizationModels);
    byId("copyPromptViewer").addEventListener("click", () => {
      if (state.activePromptKey) copyPrompt(state.activePromptKey);
    });
    document.addEventListener("click", (event) => {
      const detailButton = event.target.closest("[data-job-detail]");
      if (detailButton) {
        event.preventDefault();
        openTaskDetails(detailButton.dataset.jobDetail);
        return;
      }
      const stopButton = event.target.closest("[data-job-stop]");
      if (stopButton) {
        event.preventDefault();
        stopTask(stopButton.dataset.jobStop);
        return;
      }
      const restartButton = event.target.closest("[data-job-restart]");
      if (restartButton) {
        event.preventDefault();
        restartTask(restartButton.dataset.jobRestart);
        return;
      }
      const viewButton = event.target.closest("[data-prompt-view]");
      if (viewButton) {
        event.preventDefault();
        openPromptViewer(viewButton.dataset.promptView);
        return;
      }
      const copyButton = event.target.closest("[data-prompt-copy]");
      if (copyButton) {
        event.preventDefault();
        copyPrompt(copyButton.dataset.promptCopy);
      }
    });
    loadData().catch((error) => notify(error.message, "danger"));
  });
})();
