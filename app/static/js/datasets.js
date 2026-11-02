(() => {
  const api = "/api/v1";
  const itemPageSize = 48;
  const state = {
    datasets: [],
    selectedId: null,
    itemFilter: "ALL",
    visibleItemCount: itemPageSize,
    publishedScenarios: [],
    publishedRecipes: [],
    publishedVisionModels: [],
    augmentationSelectedIds: new Set(),
    batchJob: null,
    batchJobDatasetId: null,
    batchPollTimer: null,
    enableCollectionAfterSettings: false,
    annotation: {
      itemId: null,
      type: null,
      boxes: [],
      start: null,
      draft: null,
      selectedBoxIndex: null,
      tool: "SELECT",
      interaction: null,
      history: [],
      dirty: false,
      preannotationMeta: null,
      itemIds: [],
      itemIndex: -1,
      viewport: {
        scale: 1,
        translateX: 0,
        translateY: 0,
        spacePressed: false,
      },
    },
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
    if (!response.ok) throw new Error(payload.detail || payload.message || "请求失败");
    return payload;
  };
  const notify = (message, type = "success") => {
    byId("datasetAlert").innerHTML = `<div class="alert alert-${type} alert-dismissible fade show">${escapeHtml(message)}<button class="btn-close" data-bs-dismiss="alert"></button></div>`;
  };
  const selected = () => state.datasets.find((item) => item.id === state.selectedId);
  const openModal = (id) => window.bootstrap.Modal.getOrCreateInstance(byId(id)).show();
  const closeModal = (id) => window.bootstrap.Modal.getOrCreateInstance(byId(id)).hide();
  const isTraining = (dataset) => dataset?.purpose === "TRAIN";
  const annotationType = (dataset) => String(dataset?.annotation_type || "NONE").toUpperCase();
  const clamp = (value, min = 0, max = 1) => Math.min(max, Math.max(min, value));
  const rounded = (value) => Math.round(value * 1000000) / 1000000;

  function sourceLabel(source) {
    return {
      OFFLINE_YOLO: "离线 YOLO 导入",
      AUTO_COLLECTION: "自动采集",
      AUTO_ROI: "ROI 自动采集",
      AUTO_ORIGINAL: "相机原图采集",
      AUGMENTED: "数据增强",
      UPLOAD: "手动上传",
    }[String(source || "UPLOAD").toUpperCase()] || "手动上传";
  }

  function purposeLabel(dataset) {
    return isTraining(dataset) ? `YOLO 训练 · ${annotationTypeLabel(annotationType(dataset))}` : "场景评测 · OK/NG";
  }

  function annotationTypeLabel(value) {
    return {
      NONE: "无需训练标注",
      DETECTION: "目标检测",
      SEGMENTATION: "目标分割",
      CLASSIFICATION: "图片分类",
    }[value] || value;
  }

  function supportsDatasetAugmentation(dataset = selected()) {
    return Boolean(
      isTraining(dataset)
      && dataset?.media_type === "IMAGE"
      && ["DETECTION", "SEGMENTATION", "CLASSIFICATION"].includes(annotationType(dataset)),
    );
  }

  function modelTaskForAnnotation(value) {
    return {
      DETECTION: "YOLO_DETECTION",
      SEGMENTATION: "YOLO_SEGMENTATION",
      CLASSIFICATION: "YOLO_CLASSIFICATION",
    }[value] || null;
  }

  function compatiblePreAnnotationModels(dataset = selected()) {
    const expectedTask = modelTaskForAnnotation(annotationType(dataset));
    return state.publishedVisionModels.filter((model) => model.available && model.task_type === expectedTask);
  }

  function setPreAnnotationStatus(message, tone = "") {
    const element = byId("annotationAiAssistantStatus");
    if (!element) return;
    element.textContent = message;
    element.className = `annotation-ai-assistant-status ${tone}`.trim();
  }

  function renderPreAnnotationModels(dataset = selected()) {
    const select = byId("annotationPreModel");
    const button = byId("runAnnotationPrelabel");
    if (!select || !button) return;
    const models = compatiblePreAnnotationModels(dataset);
    const previous = select.value;
    select.innerHTML = models.length
      ? models.map((model) => `<option value="${model.version_id}">${escapeHtml(model.model_name)} · V${escapeHtml(model.version)}</option>`).join("")
      : '<option value="">暂无匹配的已发布模型</option>';
    if (models.some((model) => String(model.version_id) === previous)) select.value = previous;
    select.disabled = !models.length;
    button.disabled = !models.length;
    if (!models.length) {
      const expectedTask = modelTaskForAnnotation(annotationType(dataset));
      setPreAnnotationStatus(expectedTask ? `请先在“模型中心”发布一个可用的 ${annotationTypeLabel(annotationType(dataset))} 模型。` : "当前数据集不支持自动预标注。", "warning");
    } else {
      setPreAnnotationStatus("模型会生成可编辑的候选标注；请检查后再保存为训练真值。");
    }
  }

  function annotationSummary(item, dataset) {
    if (item.annotation_status === "AI_SUGGESTED") return "AI 标注待复核";
    if (item.annotation_status !== "LABELED") return "待标注";
    const annotation = item.annotation_json || {};
    if (annotationType(dataset) === "DETECTION") return `已框选 ${(annotation.boxes || []).length} 个目标`;
    if (annotationType(dataset) === "CLASSIFICATION") return `类别：${annotation.label || "未填写"}`;
    if (annotationType(dataset) === "SEGMENTATION") return `已标注 ${(annotation.segments || annotation.polygons || []).length} 个轮廓`;
    return "已标注";
  }

  function itemStatus(item, dataset) {
    return isTraining(dataset)
      ? annotationSummary(item, dataset)
      : (item.ground_truth || "未标注");
  }

  function openItemPreview(itemId) {
    const dataset = selected();
    const item = dataset?.items?.find((candidate) => Number(candidate.id) === Number(itemId));
    if (!item || item.media_type === "VIDEO" || !item.file_url) return;
    byId("datasetItemPreviewTitle").textContent = item.original_name || "查看素材";
    byId("datasetItemPreviewMeta").textContent = [
      sourceLabel(item.source),
      itemStatus(item, dataset),
      dataset?.name || "数据集素材",
    ].filter(Boolean).join(" · ");
    const image = byId("datasetItemPreviewImage");
    image.src = item.file_url;
    image.alt = item.original_name || "数据集素材原图";
    const originalLink = byId("datasetItemPreviewOpenOriginal");
    originalLink.href = item.file_url;
    openModal("datasetItemPreviewModal");
  }

  function renderList() {
    const keyword = byId("datasetSearch").value.trim().toLowerCase();
    const datasets = state.datasets.filter((item) => !keyword || [item.code, item.name, item.purpose].join(" ").toLowerCase().includes(keyword));
    byId("datasetCount").textContent = datasets.length;
    byId("datasetList").innerHTML = datasets.length
      ? datasets.map((dataset) => `<button class="entity-list-item ${dataset.id === state.selectedId ? "active" : ""}" data-dataset-id="${dataset.id}" type="button"><small>${escapeHtml(purposeLabel(dataset))} · ${escapeHtml(dataset.media_type)}</small><strong>${escapeHtml(dataset.name)}</strong><div class="entity-list-meta"><span>${escapeHtml(dataset.code)}</span><span>${dataset.item_count || 0} 个素材</span></div></button>`).join("")
      : '<div class="empty-state"><span>▤</span><strong>暂无数据集</strong><p>创建一个场景评测或 YOLO 训练数据集开始管理素材。</p></div>';
  }

  function itemAnnotationGroup(item) {
    if (item.annotation_status === "AI_SUGGESTED") return "AI_SUGGESTED";
    if (item.annotation_status === "LABELED") return "LABELED";
    return "PENDING";
  }

  function filteredDatasetItems(dataset) {
    const items = dataset?.items || [];
    if (!isTraining(dataset)) return items;
    const query = byId("datasetItemSearch").value.trim().toLowerCase();
    return items.filter((item) => (
      (state.itemFilter === "ALL" || itemAnnotationGroup(item) === state.itemFilter)
      && (!query || String(item.original_name || "").toLowerCase().includes(query))
    ));
  }

  function resetItemFilters() {
    state.itemFilter = "ALL";
    state.visibleItemCount = itemPageSize;
    byId("datasetItemSearch").value = "";
  }

  function renderItemFilters(dataset, matchingCount) {
    const training = isTraining(dataset);
    byId("datasetItemFilters").hidden = !training;
    const counts = { ALL: 0, PENDING: 0, AI_SUGGESTED: 0, LABELED: 0 };
    for (const item of dataset.items || []) {
      counts.ALL += 1;
      counts[itemAnnotationGroup(item)] += 1;
    }
    byId("datasetItemFilters").querySelectorAll("[data-item-filter]").forEach((button) => {
      const active = button.dataset.itemFilter === state.itemFilter;
      button.classList.toggle("active", active);
      button.setAttribute("aria-pressed", String(active));
    });
    byId("datasetItemFilters").querySelectorAll("[data-filter-count]").forEach((element) => {
      element.textContent = counts[element.dataset.filterCount];
    });
    byId("datasetItemFilterSummary").textContent = training
      ? `显示 ${Math.min(state.visibleItemCount, matchingCount)} / ${matchingCount} 张，数据集共 ${counts.ALL} 张`
      : `显示 ${Math.min(state.visibleItemCount, matchingCount)} / ${matchingCount} 张`;
  }

  function renderItems(dataset) {
    const items = filteredDatasetItems(dataset);
    renderItemFilters(dataset, items.length);
    byId("datasetLoadMore").hidden = items.length <= state.visibleItemCount;
    byId("datasetItems").innerHTML = items.length
      ? items.slice(0, state.visibleItemCount).map((item) => {
        const media = item.media_type === "VIDEO"
          ? `<video controls src="${escapeHtml(item.file_url || "")}"></video>`
          : `<button class="dataset-item-preview" data-item-preview="${item.id}" type="button" title="点击查看原图"><img src="${escapeHtml(item.file_url || "")}" alt="${escapeHtml(item.original_name)}"><span>点击查看原图</span></button>`;
        const split = item.split ? ` · ${escapeHtml(item.split)}` : "";
        const source = ` · ${escapeHtml(sourceLabel(item.source))}`;
        const truthActions = !isTraining(dataset)
          ? `<button class="btn btn-sm ${item.ground_truth === "OK" ? "btn-success" : "btn-outline-success"}" data-item-truth="OK" data-item-id="${item.id}" type="button">OK</button><button class="btn btn-sm ${item.ground_truth === "NG" ? "btn-danger" : "btn-outline-danger"}" data-item-truth="NG" data-item-id="${item.id}" type="button">NG</button>`
          : "";
        const annotationAction = isTraining(dataset)
          ? `<button class="btn btn-sm ${item.annotation_status === "LABELED" ? "btn-outline-success" : "btn-primary"}" data-item-annotate="${item.id}" type="button">${item.annotation_status === "AI_SUGGESTED" ? "复核 AI 标注" : annotationType(dataset) === "DETECTION" ? (item.annotation_status === "LABELED" ? "编辑检测框" : "框选标注") : (item.annotation_status === "LABELED" ? "编辑标注" : "添加标注")}</button>`
          : "";
        const augmentationAction = supportsDatasetAugmentation(dataset)
          && item.annotation_status === "LABELED"
          && String(item.source || "").toUpperCase() !== "AUGMENTED"
          ? `<button class="btn btn-sm ${state.augmentationSelectedIds.has(item.id) ? "btn-success" : "btn-outline-secondary"}" data-item-augmentation-select="${item.id}" type="button">${state.augmentationSelectedIds.has(item.id) ? "已选增强" : "选作增强"}</button>`
          : "";
        const status = itemStatus(item, dataset);
        return `<article class="dataset-item-card"><div>${media}</div><div class="dataset-item-content"><strong title="${escapeHtml(item.original_name)}">${escapeHtml(item.original_name)}</strong><small>${escapeHtml(status)}${split}${source}</small><div class="dataset-item-actions">${truthActions}${annotationAction}${augmentationAction}<button class="btn btn-sm btn-outline-danger" data-item-remove="${item.id}" type="button">删除</button></div></div></article>`;
      }).join("")
      : `<div class="muted-copy">${dataset.items?.length ? "当前筛选条件下没有素材。" : "还没有素材。评测数据集请标记 OK 或 NG；YOLO 训练数据集上传后可直接框选目标。"}</div>`;
  }

  function renderUploadSelection() {
    const files = byId("datasetFile").files.length ? byId("datasetFile").files : byId("datasetFolder").files;
    const count = files.length;
    byId("datasetSelectedFiles").textContent = count ? `已选择 ${count} 个素材` : "未选择素材";
    byId("uploadDatasetItems").disabled = count === 0;
  }

  function showUploadFeedback(message, type = "") {
    const feedback = byId("datasetUploadFeedback");
    feedback.textContent = message;
    feedback.className = `dataset-upload-feedback ${type}`.trim();
    feedback.hidden = !message;
  }

  function renderDetail() {
    const dataset = selected();
    byId("datasetDetail").hidden = !dataset;
    byId("datasetDetailEmpty").hidden = Boolean(dataset);
    if (!dataset) return;

    const items = dataset.items || [];
    const markedCount = isTraining(dataset)
      ? items.filter((item) => item.annotation_status === "LABELED").length
      : items.filter((item) => item.ground_truth).length;
    const pendingAiCount = items.filter((item) => item.annotation_status === "AI_SUGGESTED").length;
    const splits = dataset.split_counts || {};
    const summary = [
      ["用途", purposeLabel(dataset)],
      ["标注方式", isTraining(dataset) ? annotationTypeLabel(annotationType(dataset)) : "OK / NG"],
      ["素材数", dataset.item_count || 0],
      ["增强素材", dataset.augmented_count || 0],
      [isTraining(dataset) ? "已完成标注" : "已标记真值", markedCount],
      ...(isTraining(dataset) ? [["AI 待复核", pendingAiCount]] : []),
      ["训练 / 验证 / 测试", `${splits.TRAIN || 0} / ${splits.VAL || 0} / ${splits.TEST || 0}`],
      ["未切分", splits.UNASSIGNED || 0],
      ["修订", `r${dataset.revision}`],
    ];
    byId("datasetDetailCode").textContent = dataset.code;
    byId("datasetDetailName").textContent = dataset.name;
    byId("datasetDetailMeta").textContent = isTraining(dataset) ? "YOLO 训练" : "场景评测";
    byId("datasetDetailMeta").className = `status-pill ${isTraining(dataset) ? "draft" : "published"}`;
    byId("openDatasetAugmentation").hidden = !supportsDatasetAugmentation(dataset);
    byId("datasetSummary").innerHTML = summary.map(([label, value]) => `<div class="summary-metric"><small>${escapeHtml(label)}</small><strong>${escapeHtml(value)}</strong></div>`).join("");
    byId("datasetUploadTruthWrap").hidden = isTraining(dataset);
    byId("datasetUploadTruth").disabled = isTraining(dataset);
    byId("datasetFile").accept = dataset.media_type === "VIDEO" ? "video/*" : "image/*";
    byId("datasetFolder").accept = dataset.media_type === "VIDEO" ? "video/*" : "image/*";
    byId("selectDatasetFolder").hidden = dataset.media_type === "VIDEO";
    const supportsOfflineYoloImport = isTraining(dataset) && dataset.media_type === "IMAGE";
    byId("offlineYoloImportActions").hidden = !supportsOfflineYoloImport;
    byId("offlineYoloArchive").disabled = !supportsOfflineYoloImport;
    byId("selectOfflineYoloArchive").disabled = !supportsOfflineYoloImport;
    byId("datasetUploadHint").textContent = isTraining(dataset) ? "训练素材" : "评测素材";
    byId("datasetItemsTitle").textContent = isTraining(dataset) ? "训练素材与标注" : "素材与真值";
    byId("datasetItemsHint").textContent = isTraining(dataset)
      ? "选择图片后完成标注。"
      : "标记 OK 或 NG。";
    const supportsBatch = supportsDatasetAugmentation(dataset);
    byId("openDatasetBatchAnnotation").hidden = !supportsBatch;
    byId("openDatasetBatchAnnotation").disabled = !compatiblePreAnnotationModels(dataset).length
      || ["QUEUED", "RUNNING"].includes(state.batchJob?.status);
    renderUploadSelection();
    renderDatasetLabels(dataset);
    renderCollectionSummary(dataset);
    renderItems(dataset);
  }

  function syncAugmentationControls() {
    document.querySelectorAll("[data-augmentation-toggle]").forEach((toggle) => {
      const enabled = toggle.checked;
      const group = toggle.dataset.augmentationToggle;
      document.querySelectorAll(`[data-augmentation-range="${group}"]`).forEach((input) => {
        input.disabled = !enabled;
      });
    });
  }

  function augmentationRange(enabledId, minimumId, maximumId, minimumKey, maximumKey, neutral) {
    if (!byId(enabledId).checked) return { [minimumKey]: neutral, [maximumKey]: neutral };
    const minimum = Number(byId(minimumId).value);
    const maximum = Number(byId(maximumId).value);
    if (!Number.isFinite(minimum) || !Number.isFinite(maximum) || minimum > maximum) {
      throw new Error("增强参数的最小值不能大于最大值，并且必须为有效数字。");
    }
    return { [minimumKey]: minimum, [maximumKey]: maximum };
  }

  function buildAugmentationPayload() {
    const copiesPerItem = Number(byId("augmentationCopiesPerItem").value);
    if (!Number.isInteger(copiesPerItem) || copiesPerItem < 1 || copiesPerItem > 20) {
      throw new Error("每张原图生成数量需为 1 到 20。");
    }
    const seedText = byId("augmentationSeed").value.trim();
    const seed = seedText ? Number(seedText) : null;
    if (seed !== null && (!Number.isInteger(seed) || seed < 0 || seed > 2147483647)) {
      throw new Error("随机种子需为 0 到 2147483647 的整数。");
    }
    return {
      copies_per_item: copiesPerItem,
      seed,
      horizontal_flip: byId("augmentationHorizontalFlip").checked,
      vertical_flip: byId("augmentationVerticalFlip").checked,
      ...augmentationRange("augmentationRotationEnabled", "augmentationRotationMin", "augmentationRotationMax", "rotation_min_degrees", "rotation_max_degrees", 0),
      ...augmentationRange("augmentationBrightnessEnabled", "augmentationBrightnessMin", "augmentationBrightnessMax", "brightness_min", "brightness_max", 1),
      ...augmentationRange("augmentationContrastEnabled", "augmentationContrastMin", "augmentationContrastMax", "contrast_min", "contrast_max", 1),
      ...augmentationRange("augmentationSaturationEnabled", "augmentationSaturationMin", "augmentationSaturationMax", "saturation_min", "saturation_max", 1),
      ...augmentationRange("augmentationBlurEnabled", "augmentationBlurMin", "augmentationBlurMax", "gaussian_blur_radius_min", "gaussian_blur_radius_max", 0),
      ...augmentationRange("augmentationNoiseEnabled", "augmentationNoiseMin", "augmentationNoiseMax", "gaussian_noise_std_min", "gaussian_noise_std_max", 0),
    };
  }

  function openDatasetAugmentation() {
    const dataset = selected();
    if (!supportsDatasetAugmentation(dataset)) {
      notify("数据增强仅支持已启用标注的图片训练数据集。", "warning");
      return;
    }
    byId("datasetAugmentationForm").reset();
    byId("augmentationCopiesPerItem").value = "1";
    byId("augmentationSeed").value = "";
    const eligibleIds = new Set((dataset.items || [])
      .filter((item) => item.annotation_status === "LABELED" && String(item.source || "").toUpperCase() !== "AUGMENTED")
      .map((item) => item.id));
    state.augmentationSelectedIds = new Set([...state.augmentationSelectedIds].filter((id) => eligibleIds.has(id)));
    byId("augmentationSelectedSourceMode").disabled = state.augmentationSelectedIds.size === 0;
    byId("augmentationSelectedCount").textContent = `已选择 ${state.augmentationSelectedIds.size} 张`;
    syncAugmentationControls();
    openModal("datasetAugmentationModal");
  }

  async function runDatasetAugmentation() {
    const dataset = selected();
    if (!supportsDatasetAugmentation(dataset)) return;
    let payload;
    try {
      payload = buildAugmentationPayload();
      if (byId("augmentationSelectedSourceMode").checked) {
        payload.source_item_ids = [...state.augmentationSelectedIds];
      }
    } catch (error) {
      notify(error.message, "warning");
      return;
    }
    const hasTransform = payload.horizontal_flip || payload.vertical_flip || [
      [payload.rotation_min_degrees, payload.rotation_max_degrees, 0],
      [payload.brightness_min, payload.brightness_max, 1],
      [payload.contrast_min, payload.contrast_max, 1],
      [payload.saturation_min, payload.saturation_max, 1],
      [payload.gaussian_blur_radius_min, payload.gaussian_blur_radius_max, 0],
      [payload.gaussian_noise_std_min, payload.gaussian_noise_std_max, 0],
    ].some(([minimum, maximum, neutral]) => minimum !== neutral || maximum !== neutral);
    if (!hasTransform) {
      notify("请至少选择一种会改变图片的增强方式。", "warning");
      return;
    }
    const button = byId("runDatasetAugmentation");
    button.disabled = true;
    button.textContent = "生成中…";
    try {
      const result = await request(`${api}/datasets/${dataset.id}/augmentations`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      closeModal("datasetAugmentationModal");
      await selectDataset(dataset.id);
      const skipped = Number(result.skipped_non_training_count || 0);
      const duplicate = Number(result.skipped_duplicate_count || 0);
      const notes = [
        skipped ? `${skipped} 张验证/测试素材未增强` : "",
        duplicate ? `跳过 ${duplicate} 张重复图片` : "",
      ].filter(Boolean).join("；");
      notify(`已生成 ${result.created_count || 0} 张增强训练素材。${notes ? ` ${notes}。` : ""}`);
    } catch (error) {
      notify(error.message, "danger");
    } finally {
      button.disabled = false;
      button.textContent = "生成增强素材";
    }
  }

  function datasetLabels(dataset = selected()) {
    const annotationLabels = (dataset?.items || []).flatMap((item) => {
      const annotation = item.annotation_json || {};
      if (Array.isArray(annotation.boxes)) return annotation.boxes.map((box) => box.label);
      if (Array.isArray(annotation.segments)) return annotation.segments.map((segment) => segment.label);
      return annotation.label ? [annotation.label] : [];
    });
    return [...new Set([...(dataset?.labels || []), ...annotationLabels]
      .map((label) => String(label || "").trim())
      .filter(Boolean))].sort((left, right) => left.localeCompare(right));
  }

  function renderLabelOptions() {
    byId("annotationLabelOptions").innerHTML = datasetLabels()
      .map((label) => `<option value="${escapeHtml(label)}"></option>`)
      .join("");
  }

  function classificationLabels(dataset = selected()) {
    return [...new Set((dataset?.labels || [])
      .map((label) => String(label || "").trim())
      .filter(Boolean))].sort((left, right) => left.localeCompare(right));
  }

  function renderClassificationLabelOptions(dataset = selected(), selectedLabel = "") {
    const select = byId("classificationAnnotationLabel");
    const labels = classificationLabels(dataset);
    const requested = String(selectedLabel || "").trim();
    const matched = labels.find((label) => label.toLocaleLowerCase() === requested.toLocaleLowerCase());
    select.innerHTML = [
      `<option value="">${labels.length ? "请选择图片类别" : "请先在数据集类别中添加选项"}</option>`,
      ...labels.map((label) => `<option value="${escapeHtml(label)}">${escapeHtml(label)}</option>`),
    ].join("");
    select.disabled = labels.length === 0;
    select.value = matched || (requested ? "" : labels.length === 1 ? labels[0] : "");
    return Boolean(matched);
  }

  function renderDatasetLabels(dataset) {
    const labels = datasetLabels(dataset);
    byId("datasetLabelList").innerHTML = labels.length
      ? labels.map((label) => `<span class="dataset-label-chip">${escapeHtml(label)}<button type="button" data-dataset-label-remove="${escapeHtml(label)}" aria-label="删除 ${escapeHtml(label)}">×</button></span>`).join("")
      : '<span class="muted-copy">尚未维护类别。可在这里先添加，也可在标注时输入新类别。</span>';
    renderLabelOptions();
  }

  function renderCollectionSummary(dataset) {
    const container = byId("datasetCollectionSummary");
    const scope = String(dataset.collection_scope || "ROI").toUpperCase();
    const scene = state.publishedScenarios.find((item) => Number(item.scenario_version_id) === Number(dataset.collection_scenario_version_id));
    const recipe = state.publishedRecipes.find((item) => Number(item.id) === Number(dataset.collection_recipe_id));
    const sourceId = scope === "ORIGINAL" ? dataset.collection_recipe_id : dataset.collection_scenario_version_id;
    const toggle = byId("datasetAutoCollectToggle");
    toggle.checked = Boolean(dataset.auto_collect_enabled);
    toggle.disabled = dataset.media_type !== "IMAGE";
    byId("datasetAutoCollectState").textContent = dataset.auto_collect_enabled ? "已启用" : "已关闭";
    byId("openDatasetCollectionSettings").disabled = dataset.media_type !== "IMAGE";
    if (!dataset.auto_collect_enabled) {
      const sourceName = scope === "ORIGINAL" ? recipeLabel(recipe) || `工艺配方 #${sourceId}` : scene?.scenario_name || `场景版本 #${sourceId}`;
      container.innerHTML = `<span class="muted-copy">已关闭${sourceId ? ` · 来源：${escapeHtml(sourceName)}` : " · 请先设置采集来源"}</span>`;
      return;
    }
    if (scope === "ORIGINAL") {
      container.innerHTML = `<span class="status-pill published">相机原图采集</span><strong>${escapeHtml(recipeLabel(recipe) || `工艺配方 #${dataset.collection_recipe_id}`)}</strong><span>每张原图按内容去重；达到 ${escapeHtml(dataset.auto_collect_limit || 1000)} 张后自动停止。</span>`;
      return;
    }
    container.innerHTML = `<span class="status-pill published">ROI 采集</span><strong>${escapeHtml(scene?.scenario_name || `场景版本 #${dataset.collection_scenario_version_id}`)}</strong><span>每个生产 ROI 按内容去重；达到 ${escapeHtml(dataset.auto_collect_limit || 1000)} 张后自动停止。</span>`;
  }

  function renderBatchProgress() {
    const panel = byId("datasetBatchProgress");
    const job = state.batchJobDatasetId === state.selectedId ? state.batchJob : null;
    panel.hidden = !job;
    if (!job) return;
    const result = job.result || {};
    const progress = result.progress || {};
    const total = Number(result.total || 0);
    const processed = Number(result.processed || 0);
    const percent = Number.isFinite(Number(progress.percent)) ? Number(progress.percent) : total ? processed / total * 100 : 0;
    const status = { QUEUED: "排队中", RUNNING: "执行中", COMPLETED: "已完成", FAILED: "失败" }[job.status] || job.status;
    const failures = (result.failures || []).slice(0, 3).map((failure) => `<li>${escapeHtml(failure.name)}：${escapeHtml(failure.error)}</li>`).join("");
    panel.innerHTML = `<div class="dataset-batch-heading"><strong>批量 AI 标注 · ${escapeHtml(status)}</strong><span>${processed} / ${total}</span></div><div class="progress" role="progressbar" aria-valuenow="${Math.round(percent)}" aria-valuemin="0" aria-valuemax="100"><div class="progress-bar" style="width:${Math.max(0, Math.min(100, percent))}%"></div></div><small>待复核 ${Number(result.suggested || 0)} · 保留/跳过 ${Number(result.skipped || 0)} · 失败 ${Number(result.failed || 0)}</small>${job.error_message ? `<p class="text-danger mb-0">${escapeHtml(job.error_message)}</p>` : ""}${failures ? `<details><summary>查看失败样本</summary><ul>${failures}</ul></details>` : ""}`;
  }

  async function loadBatchJob() {
    clearTimeout(state.batchPollTimer);
    const dataset = selected();
    if (!dataset || !supportsDatasetAugmentation(dataset)) {
      state.batchJob = null;
      state.batchJobDatasetId = null;
      renderBatchProgress();
      return;
    }
    const previous = state.batchJobDatasetId === dataset.id ? state.batchJob : null;
    const job = await request(`${api}/datasets/${dataset.id}/pre-annotation-jobs`);
    if (selected()?.id !== dataset.id) return;
    state.batchJob = job;
    state.batchJobDatasetId = dataset.id;
    renderBatchProgress();
    byId("openDatasetBatchAnnotation").disabled = !compatiblePreAnnotationModels(dataset).length
      || ["QUEUED", "RUNNING"].includes(job?.status);
    if (previous && ["QUEUED", "RUNNING"].includes(previous.status) && !["QUEUED", "RUNNING"].includes(job?.status)) {
      const refreshed = await request(`${api}/datasets/${dataset.id}`);
      const index = state.datasets.findIndex((entry) => entry.id === dataset.id);
      if (index >= 0) state.datasets[index] = refreshed;
      renderList();
      renderDetail();
    }
    if (["QUEUED", "RUNNING"].includes(job?.status)) {
      state.batchPollTimer = setTimeout(() => loadBatchJob().catch((error) => notify(error.message, "danger")), 2500);
    }
  }

  function openBatchAnnotation() {
    const dataset = selected();
    if (!supportsDatasetAugmentation(dataset)) return;
    const models = compatiblePreAnnotationModels(dataset);
    byId("datasetBatchModel").innerHTML = models.length
      ? models.map((model) => `<option value="${model.version_id}">${escapeHtml(model.model_name)} · V${escapeHtml(model.version)}</option>`).join("")
      : '<option value="">暂无可用模型</option>';
    byId("datasetBatchCount").textContent = `将处理 ${dataset.items.filter((item) => item.media_type === "IMAGE" && item.source !== "AUGMENTED").length} 张原始图片。`;
    byId("datasetBatchExisting").value = "PRESERVE";
    openModal("datasetBatchAnnotationModal");
  }

  async function startBatchAnnotation() {
    const dataset = selected();
    if (!dataset) return;
    const modelId = Number(byId("datasetBatchModel").value);
    const confidence = Number(byId("datasetBatchConfidence").value);
    const mode = byId("datasetBatchExisting").value;
    if (!modelId || !Number.isFinite(confidence) || confidence < 0 || confidence > 1) {
      notify("请选择可用模型，并输入 0 到 1 之间的置信度。", "warning");
      return;
    }
    if (mode === "REPLACE" && !window.confirm("替换模式会覆盖已有标注，并使相关增强图失效。旧标注会保存在 AI 标注元数据中，是否继续？")) return;
    const button = byId("startDatasetBatchAnnotation");
    button.disabled = true;
    try {
      state.batchJob = await request(`${api}/datasets/${dataset.id}/pre-annotation-jobs`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ vision_model_version_id: modelId, confidence, existing_annotations: mode }),
      });
      state.batchJobDatasetId = dataset.id;
      closeModal("datasetBatchAnnotationModal");
      renderBatchProgress();
      notify("批量自动标注已排队，结果会标记为待复核。", "success");
      await loadBatchJob();
    } catch (error) {
      notify(error.message, "danger");
    } finally {
      button.disabled = false;
    }
  }

  function syncCollectionSettingsMode() {
    const original = byId("collectionSettingsScope").value === "ORIGINAL";
    byId("collectionSettingsScenarioField").hidden = original;
    byId("collectionSettingsRecipeField").hidden = !original;
  }

  function openCollectionSettings(enableAfterSave = false) {
    const dataset = selected();
    if (!dataset || dataset.media_type !== "IMAGE") return;
    state.enableCollectionAfterSettings = enableAfterSave;
    byId("collectionSettingsScope").value = dataset.collection_scope || "ROI";
    byId("collectionSettingsScenario").innerHTML = [
      '<option value="">请选择场景</option>',
      ...state.publishedScenarios.map((scene) => `<option value="${scene.scenario_version_id}">${escapeHtml(scene.scenario_name)} · V${escapeHtml(scene.version)}</option>`),
    ].join("");
    byId("collectionSettingsRecipe").innerHTML = [
      '<option value="">请选择工艺配方</option>',
      ...state.publishedRecipes.map((recipe) => `<option value="${recipe.id}">${escapeHtml(recipeLabel(recipe))}</option>`),
    ].join("");
    byId("collectionSettingsScenario").value = dataset.collection_scenario_version_id || "";
    byId("collectionSettingsRecipe").value = dataset.collection_recipe_id || "";
    byId("collectionSettingsLimit").value = dataset.auto_collect_limit || 1000;
    syncCollectionSettingsMode();
    openModal("datasetCollectionSettingsModal");
  }

  async function saveCollectionSettings() {
    const dataset = selected();
    if (!dataset) return;
    const original = byId("collectionSettingsScope").value === "ORIGINAL";
    const sourceId = Number(byId(original ? "collectionSettingsRecipe" : "collectionSettingsScenario").value);
    const limit = Number(byId("collectionSettingsLimit").value);
    if (!sourceId || !Number.isInteger(limit) || limit < 1 || limit > 100000) {
      notify("请选择已发布的采集来源，并填写 1 到 100000 之间的数量。", "warning");
      return;
    }
    try {
      await request(`${api}/datasets/${dataset.id}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          collection_scope: original ? "ORIGINAL" : "ROI",
          collection_scenario_version_id: original ? null : sourceId,
          collection_recipe_id: original ? sourceId : null,
          auto_collect_limit: limit,
          auto_collect_enabled: dataset.auto_collect_enabled || state.enableCollectionAfterSettings,
        }),
      });
      state.enableCollectionAfterSettings = false;
      closeModal("datasetCollectionSettingsModal");
      await loadData();
      notify("自动采集设置已保存。", "success");
    } catch (error) {
      notify(error.message, "danger");
    }
  }

  async function toggleDatasetCollection() {
    const dataset = selected();
    if (!dataset) return;
    const enabled = byId("datasetAutoCollectToggle").checked;
    const sourceId = dataset.collection_scope === "ORIGINAL" ? dataset.collection_recipe_id : dataset.collection_scenario_version_id;
    if (enabled && !sourceId) {
      byId("datasetAutoCollectToggle").checked = false;
      openCollectionSettings(true);
      return;
    }
    try {
      await request(`${api}/datasets/${dataset.id}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ auto_collect_enabled: enabled }),
      });
      await loadData();
      notify(enabled ? "已启用生产素材自动采集。" : "已关闭生产素材自动采集。", "success");
    } catch (error) {
      byId("datasetAutoCollectToggle").checked = Boolean(dataset.auto_collect_enabled);
      notify(error.message, "danger");
    }
  }

  async function loadPublishedScenarios() {
    try {
      state.publishedScenarios = await request(`${api}/scenarios/published`);
    } catch {
      state.publishedScenarios = [];
    }
    fillCollectionScenarioSelect();
    if (selected()) renderDetail();
  }

  function recipeLabel(recipe) {
    if (!recipe) return "";
    const route = [recipe.line_code, recipe.material_code, recipe.process_code, recipe.camera_code]
      .filter(Boolean)
      .join(" · ");
    const capture = recipe.capture_index ? `第 ${recipe.capture_index} 次拍照` : "";
    return [recipe.name || recipe.code, route, capture].filter(Boolean).join(" · ");
  }

  async function loadPublishedRecipes() {
    try {
      const recipes = await request(`${api}/configuration/recipes`);
      state.publishedRecipes = recipes.filter((item) => String(item.status || "").toUpperCase() === "PUBLISHED");
    } catch {
      state.publishedRecipes = [];
    }
    fillCollectionRecipeSelect();
    if (selected()) renderDetail();
  }

  async function loadPublishedVisionModels() {
    try {
      state.publishedVisionModels = await request(`${api}/vision-models/published`);
    } catch {
      state.publishedVisionModels = [];
    }
    if (byId("datasetAnnotationModal")?.classList.contains("show")) renderPreAnnotationModels();
    if (selected()) renderDetail();
  }

  function fillCollectionScenarioSelect() {
    const select = byId("datasetCollectionScenario");
    if (!select) return;
    const current = select.value;
    select.innerHTML = [
      '<option value="">不自动采集</option>',
      ...state.publishedScenarios.map((item) => `<option value="${item.scenario_version_id}">${escapeHtml(item.scenario_name)} · V${escapeHtml(item.version)}</option>`),
    ].join("");
    if ([...select.options].some((option) => option.value === current)) select.value = current;
  }

  function fillCollectionRecipeSelect() {
    const select = byId("datasetCollectionRecipe");
    if (!select) return;
    const current = select.value;
    select.innerHTML = [
      '<option value="">请选择工艺配方</option>',
      ...state.publishedRecipes.map((item) => `<option value="${item.id}">${escapeHtml(recipeLabel(item))}</option>`),
    ].join("");
    if ([...select.options].some((option) => option.value === current)) select.value = current;
  }

  function syncDatasetCollectionMode() {
    const scope = String(byId("datasetCollectionScope").value || "ROI").toUpperCase();
    const collectOriginal = scope === "ORIGINAL";
    byId("datasetCollectionScenarioField").hidden = collectOriginal;
    byId("datasetCollectionRecipeField").hidden = !collectOriginal;
    byId("datasetAutoCollectEnabledLabel").textContent = collectOriginal
      ? "启用自动采集相机原图"
      : "启用自动采集 ROI";
    byId("datasetCollectionScopeHint").textContent = collectOriginal
      ? "关联已发布工艺配方后，detect 接口中的完整相机原图会进入本数据集。"
      : "关联已发布场景后，生产检测的 ROI 局部图会进入本数据集。";
    if (collectOriginal) {
      byId("datasetCollectionScenario").value = "";
    } else {
      byId("datasetCollectionRecipe").value = "";
    }
  }

  async function loadData(keep = true) {
    state.datasets = await request(`${api}/datasets`);
    if (!keep || !state.datasets.some((item) => item.id === state.selectedId)) {
      state.selectedId = state.datasets[0]?.id ?? null;
      resetItemFilters();
      state.augmentationSelectedIds.clear();
      state.batchJob = null;
      state.batchJobDatasetId = null;
    }
    renderList();
    renderDetail();
    await loadBatchJob();
  }

  async function selectDataset(id) {
    if (state.selectedId !== Number(id)) {
      resetItemFilters();
      state.augmentationSelectedIds.clear();
      showUploadFeedback("");
      state.batchJob = null;
      state.batchJobDatasetId = null;
    }
    state.selectedId = Number(id);
    const dataset = await request(`${api}/datasets/${state.selectedId}`);
    const index = state.datasets.findIndex((item) => item.id === dataset.id);
    if (index >= 0) state.datasets[index] = dataset;
    renderList();
    renderDetail();
    await loadBatchJob();
  }

  function syncDatasetCreateMode() {
    const purpose = byId("datasetPurpose").value;
    const isYoloTraining = purpose === "TRAIN";
    const annotationField = byId("annotationTypeField");
    const annotationSelect = byId("datasetAnnotationType");
    annotationField.hidden = !isYoloTraining;
    annotationSelect.disabled = !isYoloTraining;
    if (isYoloTraining && annotationSelect.value === "NONE") annotationSelect.value = "DETECTION";
    if (!isYoloTraining) annotationSelect.value = "NONE";
    byId("datasetCreateHint").innerHTML = isYoloTraining
      ? "<strong>YOLO 训练数据集：</strong>上传图片后，在页面中框选目标并填写类别名称；类别需与后续选择的训练模型一致。"
      : "<strong>场景评测数据集：</strong>只需上传图片并人工标记 OK 或 NG，用于评测或优化 VLM 场景。";
  }

  function openDatasetCreate() {
    byId("datasetCreateForm").reset();
    fillCollectionScenarioSelect();
    fillCollectionRecipeSelect();
    byId("datasetCollectionScope").value = "ROI";
    byId("datasetAutoCollectEnabled").checked = false;
    syncDatasetCollectionMode();
    syncDatasetCreateMode();
    openModal("datasetCreateModal");
  }

  function createDatasetCode(purpose, mediaType) {
    const now = new Date();
    const timestamp = [
      now.getFullYear(),
      String(now.getMonth() + 1).padStart(2, "0"),
      String(now.getDate()).padStart(2, "0"),
      String(now.getHours()).padStart(2, "0"),
      String(now.getMinutes()).padStart(2, "0"),
      String(now.getSeconds()).padStart(2, "0"),
    ].join("");
    const suffix = Math.random().toString(36).slice(2, 8).toUpperCase();
    return `DATASET_${purpose}_${mediaType}_${timestamp}_${suffix}`;
  }

  async function createDataset() {
    const form = byId("datasetCreateForm");
    if (!form.reportValidity()) return;
    const payload = Object.fromEntries(new FormData(form).entries());
    payload.name = String(payload.name || "").trim();
    if (!payload.name) {
      notify("请填写数据集名称。", "warning");
      form.elements.name.focus();
      return;
    }
    payload.code = createDatasetCode(payload.purpose, payload.media_type);
    if (byId("datasetPurpose").value === "TEST") payload.annotation_type = "NONE";
    payload.labels = byId("datasetInitialLabels").value
      .split(/[,，\n]/)
      .map((label) => label.trim())
      .filter(Boolean);
    payload.collection_scope = byId("datasetCollectionScope").value;
    payload.collection_scenario_version_id = Number(byId("datasetCollectionScenario").value) || null;
    payload.collection_recipe_id = Number(byId("datasetCollectionRecipe").value) || null;
    payload.auto_collect_enabled = byId("datasetAutoCollectEnabled").checked;
    payload.auto_collect_limit = Number(byId("datasetAutoCollectLimit").value) || 1000;
    try {
      const result = await request(`${api}/datasets`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      closeModal("datasetCreateModal");
      form.reset();
      await loadData(false);
      await selectDataset(result.id);
      notify("数据集已创建。");
    } catch (error) {
      notify(error.message, "danger");
    }
  }

  async function uploadItem(event) {
    event.preventDefault();
    const dataset = selected();
    if (!dataset) return;
    const files = [...(byId("datasetFolder").files.length ? byId("datasetFolder").files : byId("datasetFile").files)];
    if (!files.length) {
      showUploadFeedback("请先选择图片或文件夹。", "warning");
      notify("请选择要上传的素材。", "warning");
      return;
    }
    const data = new FormData();
    files.forEach((file) => data.append("files", file, file.name));
    if (!isTraining(dataset) && byId("datasetUploadTruth").value) data.append("ground_truth", byId("datasetUploadTruth").value);
    const uploadButton = byId("uploadDatasetItems");
    uploadButton.disabled = true;
    uploadButton.textContent = "上传中…";
    showUploadFeedback(`正在上传 ${files.length} 个素材…`);
    try {
      const result = await request(`${api}/datasets/${dataset.id}/items/batch`, { method: "POST", body: data });
      byId("datasetUploadForm").reset();
      byId("datasetFolder").value = "";
      await selectDataset(dataset.id);
      const skipped = result.skipped || [];
      const skippedDetails = skipped.slice(0, 3).map((item) => `${item.filename}（${item.reason === "重复" ? "图片内容重复" : item.reason}）`).join("、");
      const skippedMessage = skipped.length ? `，跳过 ${skipped.length} 个：${skippedDetails}${skipped.length > 3 ? "等" : ""}` : "";
      const message = result.created_count
        ? `已新增 ${result.created_count} 个素材${skippedMessage}。${isTraining(dataset) ? "请继续维护标注。" : "请继续标记 OK 或 NG。"}`
        : `未新增素材${skippedMessage}。即使文件名不同，内容相同的图片也不会重复收录。`;
      const type = result.created_count ? (skipped.length ? "warning" : "success") : "warning";
      showUploadFeedback(message, type);
      notify(message, type);
    } catch (error) {
      showUploadFeedback(`上传失败：${error.message}`, "danger");
      notify(error.message, "danger");
    } finally {
      uploadButton.textContent = "开始上传";
      renderUploadSelection();
    }
  }

  async function importOfflineYoloArchive() {
    const dataset = selected();
    const archive = byId("offlineYoloArchive").files[0];
    if (!dataset || !archive) {
      notify("请选择包含图片和标签文件的 ZIP 标注包。", "warning");
      return;
    }
    if (!isTraining(dataset) || dataset.media_type !== "IMAGE") {
      notify("离线 YOLO 标注仅支持图片训练数据集。", "warning");
      return;
    }
    const data = new FormData();
    data.append("archive", archive, archive.name);
    const importButton = byId("selectOfflineYoloArchive");
    importButton.disabled = true;
    importButton.textContent = "正在导入…";
    try {
      const result = await request(`${api}/datasets/${dataset.id}/imports/yolo`, { method: "POST", body: data });
      byId("offlineYoloArchive").value = "";
      await selectDataset(dataset.id);
      const skipped = result.skipped_count ? `，跳过 ${result.skipped_count} 个重复素材` : "";
      const negative = result.negative_count ? `，其中 ${result.negative_count} 张为空目标样本` : "";
      const labels = (result.labels || []).length ? ` 已同步 ${result.labels.length} 个类别。` : "";
      notify(`离线 YOLO 标注已导入：${result.created_count} 张${skipped}${negative}。${labels}`);
    } catch (error) {
      notify(error.message, "danger");
    } finally {
      importButton.textContent = "导入标注包";
      importButton.disabled = false;
    }
  }

  async function saveDatasetLabels(labels) {
    const dataset = selected();
    if (!dataset) return;
    try {
      await request(`${api}/datasets/${dataset.id}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ labels }),
      });
      await selectDataset(dataset.id);
    } catch (error) {
      notify(error.message, "danger");
    }
  }

  async function addDatasetLabel() {
    const label = byId("datasetLabelInput").value.trim();
    if (!label) {
      notify("请填写类别名称。", "warning");
      return;
    }
    const labels = datasetLabels();
    if (!labels.some((item) => item.toLocaleLowerCase() === label.toLocaleLowerCase())) labels.push(label);
    await saveDatasetLabels(labels);
    byId("datasetLabelInput").value = "";
  }

  async function addAnnotationLabel() {
    const label = byId("annotationCurrentLabel").value.trim();
    if (!label) {
      notify("请先填写要新增的类别名称。", "warning");
      return;
    }
    const labels = datasetLabels();
    if (!labels.some((item) => item.toLocaleLowerCase() === label.toLocaleLowerCase())) {
      labels.push(label);
      await saveDatasetLabels(labels);
    }
    renderLabelOptions();
    renderAnnotationLabels();
    notify(`类别“${label}”已可用于当前数据集。`);
  }

  async function updateTruth(itemId, truth) {
    const dataset = selected();
    if (!dataset) return;
    try {
      await request(`${api}/datasets/${dataset.id}/items/${itemId}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ground_truth: truth }),
      });
      await selectDataset(dataset.id);
      notify(`已标记为 ${truth}。`);
    } catch (error) {
      notify(error.message, "danger");
    }
  }

  function annotationExample(dataset) {
    if (annotationType(dataset) === "CLASSIFICATION") return { label: "harness" };
    if (annotationType(dataset) === "SEGMENTATION") return { segments: [{ label: "harness", points: [[0.1, 0.2], [0.4, 0.2], [0.4, 0.5], [0.1, 0.5]] }] };
    return { boxes: [] };
  }

  function normalizeBoxes(annotation, image) {
    const width = image.naturalWidth || 1;
    const height = image.naturalHeight || 1;
    return (annotation?.boxes || []).flatMap((box) => {
      const x = Number(box?.x);
      const y = Number(box?.y);
      const boxWidth = Number(box?.width);
      const boxHeight = Number(box?.height);
      if (![x, y, boxWidth, boxHeight].every(Number.isFinite) || boxWidth <= 0 || boxHeight <= 0) return [];
      const usePixels = Math.max(x, y, boxWidth, boxHeight) > 1;
      const normalized = {
        label: String(box?.label || "").trim(),
        x: clamp(usePixels ? x / width : x),
        y: clamp(usePixels ? y / height : y),
        width: clamp(usePixels ? boxWidth / width : boxWidth),
        height: clamp(usePixels ? boxHeight / height : boxHeight),
        autoGenerated: Boolean(box?.auto_generated || box?.autoGenerated),
        confidence: Number.isFinite(Number(box?.confidence)) ? Number(box.confidence) : null,
      };
      normalized.width = clamp(normalized.width, 0, 1 - normalized.x);
      normalized.height = clamp(normalized.height, 0, 1 - normalized.y);
      return normalized.width > 0 && normalized.height > 0 ? [normalized] : [];
    });
  }

  const ANNOTATION_COLORS = ["#2563eb", "#16a34a", "#ea580c", "#9333ea", "#db2777", "#0891b2", "#ca8a04", "#4f46e5"];

  function cloneAnnotationBoxes(boxes = state.annotation.boxes) {
    return boxes.map((box) => ({
      label: String(box.label || ""),
      x: Number(box.x),
      y: Number(box.y),
      width: Number(box.width),
      height: Number(box.height),
      autoGenerated: Boolean(box.autoGenerated),
      confidence: Number.isFinite(Number(box.confidence)) ? Number(box.confidence) : null,
    }));
  }

  function annotationColor(label) {
    const text = String(label || "未命名");
    const seed = [...text].reduce((total, character) => ((total * 31) + character.codePointAt(0)) >>> 0, 7);
    return ANNOTATION_COLORS[seed % ANNOTATION_COLORS.length];
  }

  function selectedAnnotationBox() {
    const index = state.annotation.selectedBoxIndex;
    return Number.isInteger(index) && state.annotation.boxes[index] ? state.annotation.boxes[index] : null;
  }

  function setAnnotationDirty(dirty = true) {
    state.annotation.dirty = dirty;
    const title = byId("annotationCanvasTitle");
    if (title) title.textContent = dirty ? "检测框标注 · 未保存" : "检测框标注";
  }

  function annotationImageItems(dataset = selected()) {
    return filteredDatasetItems(dataset).filter((item) => item.media_type !== "VIDEO");
  }

  function updateAnnotationNavigation() {
    const current = state.annotation.itemIndex;
    const total = state.annotation.itemIds.length;
    byId("annotationItemPosition").textContent = total ? `第 ${current + 1} / ${total} 张` : "第 0 / 0 张";
    byId("annotationPreviousItem").disabled = current <= 0;
    byId("annotationNextItem").disabled = current < 0 || current >= total - 1;
  }

  function applyAnnotationViewport() {
    const stage = byId("detectionAnnotationStage");
    const viewport = state.annotation.viewport;
    if (!stage || !viewport) return;
    stage.style.transform = `translate(${viewport.translateX}px, ${viewport.translateY}px) scale(${viewport.scale})`;
    byId("annotationZoomValue").textContent = `${Math.round(viewport.scale * 100)}%`;
  }

  function fitAnnotationViewport() {
    const image = byId("detectionAnnotationImage");
    const stage = byId("detectionAnnotationStage");
    const wrap = byId("annotationCanvasWrap");
    if (image.naturalWidth && image.naturalHeight && wrap.clientWidth && wrap.clientHeight) {
      const fitScale = Math.min(
        1,
        Math.max(1, wrap.clientWidth - 24) / image.naturalWidth,
        Math.max(1, wrap.clientHeight - 24) / image.naturalHeight,
      );
      stage.style.width = `${Math.max(1, Math.round(image.naturalWidth * fitScale))}px`;
      stage.style.height = `${Math.max(1, Math.round(image.naturalHeight * fitScale))}px`;
    }
    state.annotation.viewport.scale = 1;
    state.annotation.viewport.translateX = 0;
    state.annotation.viewport.translateY = 0;
    applyAnnotationViewport();
    if (Number.isInteger(state.annotation.selectedBoxIndex)) renderDetectionCanvas();
  }

  function updateAnnotationToolUi() {
    const isRectangle = state.annotation.tool === "RECTANGLE";
    byId("annotationToolSelect").classList.toggle("active", !isRectangle);
    byId("annotationToolRectangle").classList.toggle("active", isRectangle);
    byId("annotationToolName").textContent = isRectangle ? "绘制矩形框" : "选择 / 移动";
    byId("detectionAnnotationStage").dataset.tool = isRectangle ? "RECTANGLE" : "SELECT";
  }

  function setAnnotationTool(tool) {
    state.annotation.tool = tool === "RECTANGLE" ? "RECTANGLE" : "SELECT";
    state.annotation.start = null;
    state.annotation.draft = null;
    updateAnnotationToolUi();
    renderDetectionCanvas();
  }

  function pushAnnotationHistory() {
    const snapshot = cloneAnnotationBoxes();
    const serialized = JSON.stringify(snapshot);
    const previous = state.annotation.history.at(-1);
    if (!previous || JSON.stringify(previous) !== serialized) {
      state.annotation.history.push(snapshot);
      if (state.annotation.history.length > 30) state.annotation.history.shift();
    }
  }

  function undoAnnotationChange() {
    const snapshot = state.annotation.history.pop();
    if (!snapshot) {
      notify("没有可撤销的标注操作。", "warning");
      return;
    }
    state.annotation.boxes = cloneAnnotationBoxes(snapshot);
    state.annotation.selectedBoxIndex = null;
    setAnnotationDirty();
    renderDetectionCanvas();
  }

  function renderAnnotationLabels() {
    const labels = datasetLabels();
    const currentLabel = byId("annotationCurrentLabel").value.trim();
    byId("annotationLabelPalette").innerHTML = labels.length
      ? labels.map((label) => `<button class="annotation-label-chip ${label === currentLabel ? "active" : ""}" style="--annotation-color:${annotationColor(label)}" data-annotation-label="${escapeHtml(label)}" type="button"><i></i>${escapeHtml(label)}</button>`).join("")
      : '<span class="muted-copy">输入类别后点击“＋”保存为可选类别。</span>';
  }

  function renderSelectedAnnotationBox() {
    const box = selectedAnnotationBox();
    const hasSelection = Boolean(box);
    byId("annotationSelectedEmpty").hidden = hasSelection;
    byId("annotationSelectedProperties").hidden = !hasSelection;
    byId("annotationRemoveSelected").disabled = !hasSelection;
    if (!box) return;
    byId("annotationSelectedLabel").value = box.label;
    byId("annotationSelectedX").value = (box.x * 100).toFixed(2);
    byId("annotationSelectedY").value = (box.y * 100).toFixed(2);
    byId("annotationSelectedWidth").value = (box.width * 100).toFixed(2);
    byId("annotationSelectedHeight").value = (box.height * 100).toFixed(2);
    const values = [["X", box.x], ["Y", box.y], ["宽", box.width], ["高", box.height]];
    if (Number.isFinite(Number(box.confidence))) values.push(["AI 置信度", Number(box.confidence)]);
    byId("annotationSelectedMetrics").innerHTML = values.map(([label, value]) => `<div><small>${label}</small><strong>${(value * 100).toFixed(1)}%</strong></div>`).join("");
  }

  function updateSelectedAnnotationLabel(value = byId("annotationSelectedLabel").value) {
    const box = selectedAnnotationBox();
    const label = String(value || "").trim();
    if (!box || !label || box.label === label) return;
    pushAnnotationHistory();
    box.label = label;
    byId("annotationCurrentLabel").value = label;
    setAnnotationDirty();
    renderDetectionCanvas();
  }

  function updateSelectedAnnotationGeometry() {
    const index = state.annotation.selectedBoxIndex;
    const box = selectedAnnotationBox();
    if (!box || !Number.isInteger(index)) return;
    const rawValues = [
      byId("annotationSelectedX").value,
      byId("annotationSelectedY").value,
      byId("annotationSelectedWidth").value,
      byId("annotationSelectedHeight").value,
    ].map(Number);
    if (rawValues.some((value) => !Number.isFinite(value))) {
      notify("位置和大小必须填写为 0 到 100 之间的数字。", "warning");
      renderSelectedAnnotationBox();
      return;
    }
    const minimum = 0.003;
    const x = clamp(rawValues[0] / 100, 0, 1 - minimum);
    const y = clamp(rawValues[1] / 100, 0, 1 - minimum);
    const width = clamp(rawValues[2] / 100, minimum, 1 - x);
    const height = clamp(rawValues[3] / 100, minimum, 1 - y);
    const next = { ...box, x: rounded(x), y: rounded(y), width: rounded(width), height: rounded(height) };
    if (["x", "y", "width", "height"].every((key) => next[key] === box[key])) {
      renderSelectedAnnotationBox();
      return;
    }
    pushAnnotationHistory();
    state.annotation.boxes[index] = next;
    setAnnotationDirty();
    renderDetectionCanvas();
  }

  function annotationBoxHandles(box, index) {
    const x = box.x;
    const y = box.y;
    const right = box.x + box.width;
    const bottom = box.y + box.height;
    const centerX = x + (box.width / 2);
    const centerY = y + (box.height / 2);
    const points = { nw: [x, y], n: [centerX, y], ne: [right, y], e: [right, centerY], se: [right, bottom], s: [centerX, bottom], sw: [x, bottom], w: [x, centerY] };
    const stageRect = byId("detectionAnnotationStage").getBoundingClientRect();
    const hitWidth = 16 / Math.max(stageRect.width, 1);
    const hitHeight = 16 / Math.max(stageRect.height, 1);
    const dotWidth = 5 / Math.max(stageRect.width, 1);
    const dotHeight = 5 / Math.max(stageRect.height, 1);
    return Object.entries(points).map(([handle, [handleX, handleY]]) => `<rect class="annotation-svg-handle" data-box-index="${index}" data-annotation-handle="${handle}" x="${handleX - (hitWidth / 2)}" y="${handleY - (hitHeight / 2)}" width="${hitWidth}" height="${hitHeight}"></rect><rect class="annotation-svg-handle-dot" x="${handleX - (dotWidth / 2)}" y="${handleY - (dotHeight / 2)}" width="${dotWidth}" height="${dotHeight}"></rect>`).join("");
  }

  function detectionBoxMarkup(box, index, draft = false) {
    const selected = !draft && index === state.annotation.selectedBoxIndex;
    const color = draft ? "#f79009" : annotationColor(box.label);
    const handles = selected ? annotationBoxHandles(box, index) : "";
    return `<g class="annotation-svg-object" data-box-index="${index}" style="--annotation-color:${color}"><rect class="annotation-svg-box ${draft ? "annotation-svg-draft" : ""} ${box.autoGenerated ? "ai-candidate" : ""} ${selected ? "selected" : ""}" data-box-index="${index}" x="${box.x}" y="${box.y}" width="${box.width}" height="${box.height}"></rect>${handles}</g>`;
  }

  function detectionDrawOriginMarkup() {
    const interaction = state.annotation.interaction;
    const point = state.annotation.start;
    if (interaction?.kind !== "DRAW" || interaction.dragged || !point) return "";
    return `<circle class="annotation-svg-origin" cx="${point.x}" cy="${point.y}" r="0.008"></circle>`;
  }

  function renderDetectionCanvas() {
    const svg = byId("detectionAnnotationSvg");
    const boxes = state.annotation.boxes;
    svg.innerHTML = boxes.map((box, index) => detectionBoxMarkup(box, index)).join("")
      + (state.annotation.draft ? detectionBoxMarkup(state.annotation.draft, boxes.length, true) : "")
      + detectionDrawOriginMarkup();
    const list = byId("detectionAnnotationList");
    list.innerHTML = boxes.length
      ? boxes.map((box, index) => {
        const candidate = box.autoGenerated ? ` · AI 候选${Number.isFinite(Number(box.confidence)) ? ` ${(Number(box.confidence) * 100).toFixed(0)}%` : ""}` : "";
        return `<div class="annotation-box-row"><button class="annotation-box-select ${index === state.annotation.selectedBoxIndex ? "active" : ""}" style="--annotation-color:${annotationColor(box.label)}" data-box-select="${index}" type="button"><span class="annotation-box-color-dot"></span><span><strong>${escapeHtml(box.label || `目标 ${index + 1}`)}</strong><small>对象 ${index + 1} · ${(box.width * 100).toFixed(1)}% × ${(box.height * 100).toFixed(1)}%${candidate}</small></span></button><button class="btn btn-sm btn-outline-danger annotation-box-remove" data-box-remove="${index}" type="button" title="删除对象">×</button></div>`;
      }).join("")
      : '<div class="annotation-empty">选择“框选”工具，输入类别后拖动鼠标绘制检测框。没有目标的负样本可保持为空。</div>';
    byId("annotationObjectCount").textContent = boxes.length;
    renderAnnotationLabels();
    renderSelectedAnnotationBox();
    updateAnnotationToolUi();
    updateAnnotationNavigation();
  }

  function pointFromEvent(event) {
    const rect = byId("detectionAnnotationSvg").getBoundingClientRect();
    if (!rect.width || !rect.height) return { x: 0, y: 0, inside: false };
    const rawX = (event.clientX - rect.left) / rect.width;
    const rawY = (event.clientY - rect.top) / rect.height;
    return {
      x: clamp(rawX),
      y: clamp(rawY),
      inside: rawX >= 0 && rawX <= 1 && rawY >= 0 && rawY <= 1,
    };
  }

  function annotationDragExceeded(event, interaction) {
    return Math.hypot(
      event.clientX - interaction.startClientX,
      event.clientY - interaction.startClientY,
    ) >= 4;
  }

  function boxFromPoints(first, second, label) {
    const x = Math.min(first.x, second.x);
    const y = Math.min(first.y, second.y);
    return {
      label,
      x: rounded(x),
      y: rounded(y),
      width: rounded(Math.abs(second.x - first.x)),
      height: rounded(Math.abs(second.y - first.y)),
    };
  }

  function resizeAnnotationBox(original, handle, current) {
    const minimum = 0.003;
    const deltaX = current.x - state.annotation.interaction.start.x;
    const deltaY = current.y - state.annotation.interaction.start.y;
    let left = original.x;
    let top = original.y;
    let right = original.x + original.width;
    let bottom = original.y + original.height;
    if (handle.includes("w")) left = clamp(original.x + deltaX, 0, right - minimum);
    if (handle.includes("e")) right = clamp((original.x + original.width) + deltaX, left + minimum, 1);
    if (handle.includes("n")) top = clamp(original.y + deltaY, 0, bottom - minimum);
    if (handle.includes("s")) bottom = clamp((original.y + original.height) + deltaY, top + minimum, 1);
    return {
      ...original,
      x: rounded(left),
      y: rounded(top),
      width: rounded(right - left),
      height: rounded(bottom - top),
    };
  }

  function moveAnnotationBox(original, current) {
    const deltaX = current.x - state.annotation.interaction.start.x;
    const deltaY = current.y - state.annotation.interaction.start.y;
    return {
      ...original,
      x: rounded(clamp(original.x + deltaX, 0, 1 - original.width)),
      y: rounded(clamp(original.y + deltaY, 0, 1 - original.height)),
      width: original.width,
      height: original.height,
    };
  }

  function annotationPointerMatches(event, interaction = state.annotation.interaction) {
    return Boolean(interaction) && (interaction.pointerId === undefined || interaction.pointerId === event.pointerId);
  }

  function captureAnnotationPointer(event) {
    const captureTarget = event.currentTarget;
    if (!captureTarget?.setPointerCapture) return null;
    captureTarget.setPointerCapture(event.pointerId);
    return captureTarget;
  }

  function releaseAnnotationPointer(interaction, event) {
    const captureTarget = interaction?.captureTarget;
    if (!captureTarget?.releasePointerCapture || !event) return;
    if (!captureTarget.hasPointerCapture?.(event.pointerId)) return;
    captureTarget.releasePointerCapture(event.pointerId);
  }

  function startDetectionInteraction(event) {
    if (state.annotation.type !== "DETECTION") return;
    if (event.pointerType !== "touch" && ![0, 1].includes(event.button)) return;
    const target = event.target;
    const handle = target.closest?.("[data-annotation-handle]");
    const boxTarget = target.closest?.("[data-box-index]");
    // In selection mode, dragging empty image space pans the image.  This
    // keeps the familiar direct-manipulation behavior while boxes remain
    // click-selectable and draggable.
    const isPan = event.button === 1
      || state.annotation.viewport.spacePressed
      || (state.annotation.tool === "SELECT" && !boxTarget);
    if (isPan) {
      event.preventDefault();
      state.annotation.interaction = {
        kind: "PAN",
        pointerId: event.pointerId,
        captureTarget: captureAnnotationPointer(event),
        startClientX: event.clientX,
        startClientY: event.clientY,
        originX: state.annotation.viewport.translateX,
        originY: state.annotation.viewport.translateY,
        moved: false,
      };
      return;
    }
    if (event.button !== 0 && event.pointerType !== "touch") return;
    if (state.annotation.tool === "SELECT") {
      const index = Number((handle || boxTarget).dataset.boxIndex);
      const box = state.annotation.boxes[index];
      if (!box) return;
      event.preventDefault();
      state.annotation.selectedBoxIndex = index;
      pushAnnotationHistory();
      state.annotation.interaction = {
        kind: handle ? "RESIZE" : "MOVE",
        index,
        handle: handle?.dataset.annotationHandle || null,
        pointerId: event.pointerId,
        captureTarget: captureAnnotationPointer(event),
        start: pointFromEvent(event),
        original: { ...box },
      };
      renderDetectionCanvas();
      return;
    }
    const label = byId("annotationCurrentLabel").value.trim();
    if (!label) {
      notify("请先输入或选择当前框的类别名称。", "warning");
      byId("annotationCurrentLabel").focus();
      return;
    }
    event.preventDefault();
    state.annotation.start = pointFromEvent(event);
    state.annotation.draft = null;
    state.annotation.interaction = {
      kind: "DRAW",
      pointerId: event.pointerId,
      captureTarget: captureAnnotationPointer(event),
      startClientX: event.clientX,
      startClientY: event.clientY,
      lastPoint: state.annotation.start,
      dragged: false,
    };
    renderDetectionCanvas();
  }

  function moveDetectionInteraction(event) {
    const interaction = state.annotation.interaction;
    if (!annotationPointerMatches(event, interaction)) return;
    if (interaction?.kind === "PAN") {
      event.preventDefault();
      const deltaX = event.clientX - interaction.startClientX;
      const deltaY = event.clientY - interaction.startClientY;
      interaction.moved = interaction.moved || Math.abs(deltaX) > 2 || Math.abs(deltaY) > 2;
      state.annotation.viewport.translateX = interaction.originX + deltaX;
      state.annotation.viewport.translateY = interaction.originY + deltaY;
      applyAnnotationViewport();
      return;
    }
    if (interaction?.kind === "MOVE" || interaction?.kind === "RESIZE") {
      event.preventDefault();
      const current = pointFromEvent(event);
      interaction.lastPoint = current;
      state.annotation.boxes[interaction.index] = interaction.kind === "MOVE"
        ? moveAnnotationBox(interaction.original, current)
        : resizeAnnotationBox(interaction.original, interaction.handle || "se", current);
      setAnnotationDirty();
      renderDetectionCanvas();
      return;
    }
    if (interaction?.kind !== "DRAW" || !state.annotation.start) return;
    event.preventDefault();
    const current = pointFromEvent(event);
    if (current.inside) interaction.lastPoint = current;
    if (annotationDragExceeded(event, interaction)) interaction.dragged = true;
    if (interaction.dragged) {
      state.annotation.draft = boxFromPoints(
        state.annotation.start,
        interaction.lastPoint || state.annotation.start,
        byId("annotationCurrentLabel").value.trim(),
      );
    }
    renderDetectionCanvas();
  }

  function finishDetectionInteraction(event) {
    const interaction = state.annotation.interaction;
    if (!annotationPointerMatches(event, interaction)) return;
    event.preventDefault();
    if (interaction?.kind === "PAN" && !interaction.moved) {
      state.annotation.selectedBoxIndex = null;
    }
    if (state.annotation.start && interaction?.kind === "DRAW") {
      const endPoint = pointFromEvent(event);
      if (endPoint.inside) interaction.lastPoint = endPoint;
      const box = interaction.dragged
        ? boxFromPoints(
          state.annotation.start,
          interaction.lastPoint || state.annotation.start,
          byId("annotationCurrentLabel").value.trim(),
        )
        : null;
      state.annotation.start = null;
      state.annotation.draft = null;
      if (box && box.width >= 0.005 && box.height >= 0.005 && box.label) {
        pushAnnotationHistory();
        state.annotation.boxes.push(box);
        state.annotation.selectedBoxIndex = state.annotation.boxes.length - 1;
        if (!event.shiftKey) state.annotation.tool = "SELECT";
        setAnnotationDirty();
      }
    }
    state.annotation.interaction = null;
    releaseAnnotationPointer(interaction, event);
    renderDetectionCanvas();
  }

  function cancelDetectionDraw(event = null) {
    const interaction = state.annotation.interaction;
    if (event && !annotationPointerMatches(event, interaction)) return;
    if (interaction?.original && Number.isInteger(interaction.index)) {
      state.annotation.boxes[interaction.index] = interaction.original;
    }
    state.annotation.start = null;
    state.annotation.draft = null;
    state.annotation.interaction = null;
    if (event) releaseAnnotationPointer(interaction, event);
    renderDetectionCanvas();
  }

  function deleteSelectedAnnotationBox() {
    const index = state.annotation.selectedBoxIndex;
    if (!Number.isInteger(index) || !state.annotation.boxes[index]) return;
    pushAnnotationHistory();
    state.annotation.boxes.splice(index, 1);
    state.annotation.selectedBoxIndex = null;
    setAnnotationDirty();
    renderDetectionCanvas();
  }

  function changeAnnotationZoom(step, event = null) {
    const viewport = state.annotation.viewport;
    const previousScale = viewport.scale;
    const nextScale = clamp(Math.round((previousScale + step) * 100) / 100, 0.35, 4);
    if (nextScale === previousScale) return;
    const wrapRect = byId("annotationCanvasWrap").getBoundingClientRect();
    const stageRect = byId("detectionAnnotationStage").getBoundingClientRect();
    const centerX = stageRect.left + stageRect.width / 2;
    const centerY = stageRect.top + stageRect.height / 2;
    const focusX = event?.clientX ?? wrapRect.left + wrapRect.width / 2;
    const focusY = event?.clientY ?? wrapRect.top + wrapRect.height / 2;
    viewport.translateX += (focusX - centerX) * (1 - nextScale / previousScale);
    viewport.translateY += (focusY - centerY) * (1 - nextScale / previousScale);
    viewport.scale = nextScale;
    applyAnnotationViewport();
    if (Number.isInteger(state.annotation.selectedBoxIndex)) renderDetectionCanvas();
  }

  function navigateAnnotation(offset) {
    if (state.annotation.dirty) {
      notify("当前图片的标注尚未保存，请先保存后再切换图片。", "warning");
      return;
    }
    const nextIndex = state.annotation.itemIndex + offset;
    const itemId = state.annotation.itemIds[nextIndex];
    if (itemId) openAnnotation(itemId);
  }

  async function runAnnotationPrelabel() {
    const dataset = selected();
    const itemId = Number(byId("annotationItemId").value);
    const modelVersionId = Number(byId("annotationPreModel").value);
    const button = byId("runAnnotationPrelabel");
    if (!dataset || !itemId || !modelVersionId) {
      notify("请先选择一个可用的已发布模型。", "warning");
      return;
    }
    const type = annotationType(dataset);
    const hasExistingAnnotation = type === "DETECTION"
      ? state.annotation.boxes.length > 0
      : type === "CLASSIFICATION"
        ? Boolean(byId("classificationAnnotationLabel").value.trim())
        : Boolean(byId("annotationJson").value.trim() && byId("annotationJson").value.trim() !== JSON.stringify(annotationExample(dataset), null, 2));
    if (hasExistingAnnotation && !window.confirm("AI 预标注会替换当前页面中尚未保存的标注候选。是否继续？")) return;
    const confidence = Number(byId("annotationPreConfidence").value);
    if (!Number.isFinite(confidence) || confidence < 0 || confidence > 1) {
      notify("置信度阈值需在 0 到 1 之间。", "warning");
      byId("annotationPreConfidence").focus();
      return;
    }
    button.disabled = true;
    button.textContent = "生成中…";
    setPreAnnotationStatus("模型正在生成候选标注，请稍候…");
    try {
      const response = await request(`${api}/datasets/${dataset.id}/items/${itemId}/pre-annotations`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ vision_model_version_id: modelVersionId, confidence }),
      });
      const annotation = response.annotation || {};
      state.annotation.preannotationMeta = annotation._meta || null;
      state.annotation.history = [];
      state.annotation.selectedBoxIndex = null;
      if (type === "DETECTION") {
        state.annotation.boxes = normalizeBoxes(annotation, byId("detectionAnnotationImage"));
        if (state.annotation.boxes[0]?.label) byId("annotationCurrentLabel").value = state.annotation.boxes[0].label;
        renderDetectionCanvas();
      } else if (type === "CLASSIFICATION") {
        renderClassificationLabelOptions(dataset, annotation.label);
      } else {
        byId("annotationJson").value = JSON.stringify(annotation, null, 2);
      }
      setAnnotationDirty(true);
      const warningText = [...(response.warnings || []), (response.unknown_labels || []).length ? `待确认类别：${response.unknown_labels.join("、")}` : ""]
        .filter(Boolean)
        .join(" ");
      const modelName = response.model?.name || "所选模型";
      const candidateCount = Number(response.candidate_count || 0);
      setPreAnnotationStatus(`${modelName} 已生成 ${candidateCount} 个候选标注。${warningText ? ` ${warningText}` : " 请检查、修改后再保存。"}`, warningText ? "warning" : "success");
      notify(`AI 预标注完成：${candidateCount} 个候选，请人工确认后保存。`);
    } catch (error) {
      setPreAnnotationStatus(`AI 预标注失败：${error.message}`, "warning");
      notify(error.message, "danger");
    } finally {
      button.textContent = "AI 预标注";
      button.disabled = !compatiblePreAnnotationModels(dataset).length;
    }
  }

  function openAnnotation(itemId) {
    const dataset = selected();
    const item = dataset?.items?.find((candidate) => candidate.id === Number(itemId));
    if (!dataset || !item || !isTraining(dataset)) return;
    const type = annotationType(dataset);
    const annotationItems = annotationImageItems(dataset);
    state.annotation = {
      itemId: item.id,
      type,
      boxes: [],
      start: null,
      draft: null,
      selectedBoxIndex: null,
      tool: "SELECT",
      interaction: null,
      history: [],
      dirty: false,
      preannotationMeta: item.annotation_json?._meta || null,
      itemIds: annotationItems.map((candidate) => candidate.id),
      itemIndex: annotationItems.findIndex((candidate) => candidate.id === item.id),
      viewport: { scale: 1, translateX: 0, translateY: 0, spacePressed: false },
    };
    byId("annotationItemId").value = item.id;
    byId("annotationItemName").textContent = `样本：${item.original_name} · ${annotationTypeLabel(type)}`;
    byId("annotationDetectionEditor").hidden = type !== "DETECTION";
    byId("annotationClassificationEditor").hidden = type !== "CLASSIFICATION";
    byId("annotationJsonEditor").hidden = type !== "SEGMENTATION";
    byId("annotationHint").textContent = type === "DETECTION"
      ? "采用选择、移动和矩形框选工作台。负样本可保留为空框，训练前仍会检查类别和标注完整度。"
      : type === "CLASSIFICATION"
        ? "为整张图片选择一个类别名称。"
        : "维护分割轮廓点，类别名称需与后续训练模型的识别类别一致。";
    if (item.annotation_status === "AI_SUGGESTED") {
      const candidateLabel = item.annotation_json?._meta?.ai_candidate_label;
      byId("annotationHint").textContent = candidateLabel
        ? `AI 建议类别：${candidateLabel}。原类别已保留，请比较后选择并保存确认。`
        : "AI 已生成待复核标注；请检查类别与区域，保存后才会进入训练数据。";
    }
    const labels = datasetLabels(dataset);
    byId("annotationCurrentLabel").value = item.annotation_json?.boxes?.[0]?.label || labels[0] || "";
    renderLabelOptions();
    const existingClassificationLabel = item.annotation_json?.label || "";
    const hasKnownClassificationLabel = renderClassificationLabelOptions(dataset, existingClassificationLabel);
    if (type === "CLASSIFICATION" && existingClassificationLabel && !hasKnownClassificationLabel) {
      byId("annotationHint").textContent = "当前历史标注类别不在数据集类别中，请先在数据集类别中补充或改选标准类别后再保存。";
    }
    renderPreAnnotationModels(dataset);
    byId("annotationJson").value = JSON.stringify(Object.keys(item.annotation_json || {}).length ? item.annotation_json : annotationExample(dataset), null, 2);
    if (type === "DETECTION") {
      setAnnotationDirty(false);
      renderAnnotationLabels();
      updateAnnotationToolUi();
      updateAnnotationNavigation();
      const image = byId("detectionAnnotationImage");
      const hydrateBoxes = () => {
        if (Number(state.annotation.itemId) !== Number(item.id)) return;
        state.annotation.boxes = normalizeBoxes(item.annotation_json || {}, image);
        setAnnotationDirty(false);
        fitAnnotationViewport();
        renderDetectionCanvas();
      };
      image.onload = hydrateBoxes;
      image.src = item.file_url || "";
      if (image.complete && image.naturalWidth) hydrateBoxes();
    }
    openModal("datasetAnnotationModal");
  }

  async function saveAnnotation() {
    const dataset = selected();
    const itemId = Number(byId("annotationItemId").value);
    if (!dataset || !itemId) return;
    let annotationJson;
    const type = annotationType(dataset);
    if (type === "DETECTION") {
      const invalidBox = state.annotation.boxes.find((box) => !box.label.trim() || box.width <= 0 || box.height <= 0);
      if (invalidBox) {
        notify("每个检测框都需要填写类别名称。", "warning");
        return;
      }
      annotationJson = {
        boxes: state.annotation.boxes.map((box) => ({
          label: box.label.trim(),
          x: rounded(box.x),
          y: rounded(box.y),
          width: rounded(box.width),
          height: rounded(box.height),
        })),
      };
    } else if (type === "CLASSIFICATION") {
      const label = byId("classificationAnnotationLabel").value.trim();
      if (!label) {
        notify("请选择数据集已维护的图片类别。", "warning");
        return;
      }
      annotationJson = { label };
    } else {
      try {
        annotationJson = JSON.parse(byId("annotationJson").value);
      } catch {
        notify("分割标注 JSON 格式不正确。", "warning");
        return;
      }
    }
    if (state.annotation.preannotationMeta) {
      annotationJson._meta = { ...state.annotation.preannotationMeta, review_required: false, confirmed_at: new Date().toISOString() };
    }
    try {
      const result = await request(`${api}/datasets/${dataset.id}/items/${itemId}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ annotation_status: "LABELED", annotation_json: annotationJson }),
      });
      setAnnotationDirty(false);
      closeModal("datasetAnnotationModal");
      await selectDataset(dataset.id);
      const invalidated = Number(result.invalidated_augmented_count || 0);
      notify(invalidated ? `训练标注已保存；${invalidated} 张旧增强图已失效，可重新生成。` : "训练标注已保存。");
    } catch (error) {
      notify(error.message, "danger");
    }
  }

  async function removeItem(itemId) {
    const dataset = selected();
    if (!dataset || !confirm("删除后该素材不会再参与训练或评测。确认删除吗？")) return;
    try {
      const result = await request(`${api}/datasets/${dataset.id}/items/${itemId}`, { method: "DELETE" });
      await selectDataset(dataset.id);
      const invalidated = Number(result.invalidated_augmented_count || 0);
      notify(invalidated ? `素材已删除；其 ${invalidated} 张增强图也已失效。` : "素材已删除。");
    } catch (error) {
      notify(error.message, "danger");
    }
  }

  async function deleteDataset() {
    const dataset = selected();
    if (!dataset) return;
    const itemCount = Number(dataset.item_count || 0);
    const message = [
      `确认删除数据集“${dataset.name}”吗？`,
      itemCount ? `其中 ${itemCount} 个素材将不再参与训练、评测和自动采集。` : "该数据集当前没有素材。",
      "历史训练、评测与检测记录会保留，方便后续追溯。",
    ].join("\n");
    if (!confirm(message)) return;

    const button = byId("deleteDataset");
    button.disabled = true;
    try {
      const response = await request(`${api}/datasets/${dataset.id}`, { method: "DELETE" });
      state.selectedId = null;
      await loadData(false);
      const cancelled = Number(response.cancelled_job_count || 0);
      notify(cancelled ? `数据集已删除，已取消 ${cancelled} 个等待中的任务。` : "数据集已删除。", "success");
    } catch (error) {
      notify(error.message, "danger");
    } finally {
      button.disabled = false;
    }
  }

  document.addEventListener("DOMContentLoaded", () => {
    byId("openDatasetCreate").addEventListener("click", openDatasetCreate);
    byId("datasetPurpose").addEventListener("change", syncDatasetCreateMode);
    byId("createDataset").addEventListener("click", createDataset);
    byId("deleteDataset").addEventListener("click", deleteDataset);
    byId("openDatasetAugmentation").addEventListener("click", openDatasetAugmentation);
    byId("openDatasetBatchAnnotation").addEventListener("click", openBatchAnnotation);
    byId("startDatasetBatchAnnotation").addEventListener("click", startBatchAnnotation);
    byId("datasetAutoCollectToggle").addEventListener("change", toggleDatasetCollection);
    byId("openDatasetCollectionSettings").addEventListener("click", () => openCollectionSettings());
    byId("collectionSettingsScope").addEventListener("change", syncCollectionSettingsMode);
    byId("saveDatasetCollectionSettings").addEventListener("click", saveCollectionSettings);
    byId("runDatasetAugmentation").addEventListener("click", runDatasetAugmentation);
    byId("datasetAugmentationForm").addEventListener("submit", (event) => {
      event.preventDefault();
      runDatasetAugmentation();
    });
    document.querySelectorAll("[data-augmentation-toggle]").forEach((toggle) => {
      toggle.addEventListener("change", syncAugmentationControls);
    });
    byId("datasetCreateForm").addEventListener("submit", (event) => {
      event.preventDefault();
      createDataset();
    });
    byId("reloadDatasets").addEventListener("click", () => loadData().catch((error) => notify(error.message, "danger")));
    byId("datasetSearch").addEventListener("input", renderList);
    byId("datasetItemFilters").addEventListener("click", (event) => {
      const button = event.target.closest("[data-item-filter]");
      if (!button || !isTraining(selected())) return;
      state.itemFilter = button.dataset.itemFilter;
      state.visibleItemCount = itemPageSize;
      renderItems(selected());
    });
    byId("datasetItemSearch").addEventListener("input", () => {
      state.visibleItemCount = itemPageSize;
      renderItems(selected());
    });
    byId("datasetLoadMore").addEventListener("click", () => {
      state.visibleItemCount += itemPageSize;
      renderItems(selected());
    });
    byId("datasetCollectionScope").addEventListener("change", syncDatasetCollectionMode);
    byId("datasetCollectionScenario").addEventListener("change", (event) => {
      byId("datasetAutoCollectEnabled").checked = Boolean(event.target.value);
    });
    byId("datasetCollectionRecipe").addEventListener("change", (event) => {
      byId("datasetAutoCollectEnabled").checked = Boolean(event.target.value);
    });
    byId("datasetList").addEventListener("click", (event) => {
      const button = event.target.closest("[data-dataset-id]");
      if (button) selectDataset(button.dataset.datasetId);
    });
    byId("datasetUploadForm").addEventListener("submit", uploadItem);
    byId("selectDatasetFolder").addEventListener("click", () => byId("datasetFolder").click());
    byId("selectOfflineYoloArchive").addEventListener("click", () => byId("offlineYoloArchive").click());
    byId("datasetFolder").addEventListener("change", () => {
      if (byId("datasetFolder").files.length) {
        byId("datasetFile").value = "";
        byId("offlineYoloArchive").value = "";
      }
      showUploadFeedback("");
      renderUploadSelection();
    });
    byId("datasetFile").addEventListener("change", () => {
      if (byId("datasetFile").files.length) {
        byId("datasetFolder").value = "";
        byId("offlineYoloArchive").value = "";
      }
      showUploadFeedback("");
      renderUploadSelection();
    });
    byId("offlineYoloArchive").addEventListener("change", () => {
      if (!byId("offlineYoloArchive").files.length) return;
      byId("datasetFile").value = "";
      byId("datasetFolder").value = "";
      importOfflineYoloArchive();
    });
    byId("addDatasetLabel").addEventListener("click", addDatasetLabel);
    byId("datasetLabelInput").addEventListener("keydown", (event) => {
      if (event.key === "Enter") {
        event.preventDefault();
        addDatasetLabel();
      }
    });
    byId("datasetLabelList").addEventListener("click", (event) => {
      const button = event.target.closest("[data-dataset-label-remove]");
      if (!button) return;
      const label = button.dataset.datasetLabelRemove;
      const remaining = datasetLabels().filter((item) => item !== label);
      saveDatasetLabels(remaining);
    });
    byId("saveDatasetAnnotation").addEventListener("click", saveAnnotation);
    byId("runAnnotationPrelabel").addEventListener("click", runAnnotationPrelabel);
    byId("addAnnotationLabel").addEventListener("click", addAnnotationLabel);
    byId("undoDetectionBox").addEventListener("click", undoAnnotationChange);
    byId("cancelDetectionDraw").addEventListener("click", cancelDetectionDraw);
    byId("clearDetectionBoxes").addEventListener("click", () => {
      if (!state.annotation.boxes.length) return;
      pushAnnotationHistory();
      state.annotation.boxes = [];
      state.annotation.start = null;
      state.annotation.draft = null;
      state.annotation.selectedBoxIndex = null;
      setAnnotationDirty();
      renderDetectionCanvas();
    });
    byId("annotationToolSelect").addEventListener("click", () => setAnnotationTool("SELECT"));
    byId("annotationToolRectangle").addEventListener("click", () => setAnnotationTool("RECTANGLE"));
    byId("annotationZoomIn").addEventListener("click", () => changeAnnotationZoom(0.2));
    byId("annotationZoomOut").addEventListener("click", () => changeAnnotationZoom(-0.2));
    byId("annotationZoomFit").addEventListener("click", fitAnnotationViewport);
    byId("annotationPreviousItem").addEventListener("click", () => navigateAnnotation(-1));
    byId("annotationNextItem").addEventListener("click", () => navigateAnnotation(1));
    byId("annotationCanvasWrap").addEventListener("wheel", (event) => {
      if (!byId("datasetAnnotationModal").classList.contains("show") || state.annotation.type !== "DETECTION") return;
      event.preventDefault();
      changeAnnotationZoom(event.deltaY < 0 ? 0.12 : -0.12, event);
    }, { passive: false });
    byId("annotationCanvasWrap").addEventListener("dblclick", fitAnnotationViewport);
    byId("datasetAnnotationModal").addEventListener("shown.bs.modal", () => {
      if (state.annotation.type === "DETECTION") fitAnnotationViewport();
    });
    window.addEventListener("resize", () => {
      if (byId("datasetAnnotationModal").classList.contains("show") && state.annotation.type === "DETECTION") {
        fitAnnotationViewport();
      }
    });
    // Start on the stable canvas container and follow the pointer on document.
    // This keeps move/resize working even when the pointer leaves the SVG while
    // dragging or the SVG is re-rendered to show the updated frame.
    byId("detectionAnnotationStage").addEventListener("pointerdown", startDetectionInteraction);
    document.addEventListener("pointermove", moveDetectionInteraction, { passive: false });
    document.addEventListener("pointerup", finishDetectionInteraction, { passive: false });
    document.addEventListener("pointercancel", cancelDetectionDraw, { passive: false });
    byId("annotationCurrentLabel").addEventListener("input", renderAnnotationLabels);
    byId("annotationLabelPalette").addEventListener("click", (event) => {
      const button = event.target.closest("[data-annotation-label]");
      if (!button) return;
      byId("annotationCurrentLabel").value = button.dataset.annotationLabel;
      renderDetectionCanvas();
    });
    byId("detectionAnnotationList").addEventListener("click", (event) => {
      const button = event.target.closest("[data-box-remove]");
      const selectedButton = event.target.closest("[data-box-select]");
      if (button) {
        const index = Number(button.dataset.boxRemove);
        if (!state.annotation.boxes[index]) return;
        pushAnnotationHistory();
        state.annotation.boxes.splice(index, 1);
        state.annotation.selectedBoxIndex = null;
        setAnnotationDirty();
      } else if (selectedButton) {
        state.annotation.selectedBoxIndex = Number(selectedButton.dataset.boxSelect);
      } else {
        return;
      }
      renderDetectionCanvas();
    });
    byId("annotationSelectedLabel").addEventListener("change", (event) => updateSelectedAnnotationLabel(event.target.value));
    byId("annotationSelectedLabel").addEventListener("keydown", (event) => {
      if (event.key !== "Enter") return;
      event.preventDefault();
      updateSelectedAnnotationLabel(event.currentTarget.value);
      event.currentTarget.blur();
    });
    ["annotationSelectedX", "annotationSelectedY", "annotationSelectedWidth", "annotationSelectedHeight"].forEach((id) => {
      byId(id).addEventListener("change", updateSelectedAnnotationGeometry);
      byId(id).addEventListener("keydown", (event) => {
        if (event.key !== "Enter") return;
        event.preventDefault();
        updateSelectedAnnotationGeometry();
        event.currentTarget.blur();
      });
    });
    byId("annotationRemoveSelected").addEventListener("click", deleteSelectedAnnotationBox);
    document.addEventListener("keydown", (event) => {
      const modalOpen = byId("datasetAnnotationModal").classList.contains("show");
      if (!modalOpen || state.annotation.type !== "DETECTION") return;
      const editing = event.target.matches?.("input, textarea, select, [contenteditable='true']");
      if (event.code === "Space" && !editing) {
        state.annotation.viewport.spacePressed = true;
        event.preventDefault();
        return;
      }
      if (editing) return;
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "z") {
        event.preventDefault();
        undoAnnotationChange();
      } else if (event.key.toLowerCase() === "v") {
        event.preventDefault();
        setAnnotationTool("SELECT");
      } else if (event.key.toLowerCase() === "r") {
        event.preventDefault();
        setAnnotationTool("RECTANGLE");
      } else if (event.key === "Delete" || event.key === "Backspace") {
        event.preventDefault();
        deleteSelectedAnnotationBox();
      } else if (event.key === "Escape") {
        event.preventDefault();
        cancelDetectionDraw();
      } else if (event.key.toLowerCase() === "a") {
        event.preventDefault();
        navigateAnnotation(-1);
      } else if (event.key.toLowerCase() === "d") {
        event.preventDefault();
        navigateAnnotation(1);
      }
    });
    document.addEventListener("keyup", (event) => {
      if (event.code === "Space") state.annotation.viewport.spacePressed = false;
    });
    byId("datasetItems").addEventListener("click", (event) => {
      const previewButton = event.target.closest("[data-item-preview]");
      const truthButton = event.target.closest("[data-item-truth]");
      const annotateButton = event.target.closest("[data-item-annotate]");
      const augmentButton = event.target.closest("[data-item-augmentation-select]");
      const removeButton = event.target.closest("[data-item-remove]");
      if (previewButton) openItemPreview(previewButton.dataset.itemPreview);
      else if (truthButton) updateTruth(truthButton.dataset.itemId, truthButton.dataset.itemTruth);
      else if (annotateButton) openAnnotation(annotateButton.dataset.itemAnnotate);
      else if (augmentButton) {
        const itemId = Number(augmentButton.dataset.itemAugmentationSelect);
        if (state.augmentationSelectedIds.has(itemId)) state.augmentationSelectedIds.delete(itemId);
        else state.augmentationSelectedIds.add(itemId);
        renderItems(selected());
      }
      else if (removeButton) removeItem(removeButton.dataset.itemRemove);
    });
    syncDatasetCreateMode();
    syncDatasetCollectionMode();
    syncAugmentationControls();
    Promise.all([loadData(false), loadPublishedScenarios(), loadPublishedRecipes(), loadPublishedVisionModels()]).catch((error) => notify(error.message, "danger"));
  });
})();
