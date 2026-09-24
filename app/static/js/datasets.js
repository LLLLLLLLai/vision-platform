(() => {
  const api = "/api/v1";
  const state = {
    datasets: [],
    selectedId: null,
    publishedScenarios: [],
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

  function annotationSummary(item, dataset) {
    if (item.annotation_status !== "LABELED") return "待标注";
    const annotation = item.annotation_json || {};
    if (annotationType(dataset) === "DETECTION") return `已框选 ${(annotation.boxes || []).length} 个目标`;
    if (annotationType(dataset) === "CLASSIFICATION") return `类别：${annotation.label || "未填写"}`;
    if (annotationType(dataset) === "SEGMENTATION") return `已标注 ${(annotation.segments || annotation.polygons || []).length} 个轮廓`;
    return "已标注";
  }

  function renderList() {
    const keyword = byId("datasetSearch").value.trim().toLowerCase();
    const datasets = state.datasets.filter((item) => !keyword || [item.code, item.name, item.purpose].join(" ").toLowerCase().includes(keyword));
    byId("datasetCount").textContent = datasets.length;
    byId("datasetList").innerHTML = datasets.length
      ? datasets.map((dataset) => `<button class="entity-list-item ${dataset.id === state.selectedId ? "active" : ""}" data-dataset-id="${dataset.id}" type="button"><small>${escapeHtml(purposeLabel(dataset))} · ${escapeHtml(dataset.media_type)}</small><strong>${escapeHtml(dataset.name)}</strong><div class="entity-list-meta"><span>${escapeHtml(dataset.code)}</span><span>${dataset.item_count || 0} 个素材</span></div></button>`).join("")
      : '<div class="empty-state"><span>▤</span><strong>暂无数据集</strong><p>创建一个场景评测或 YOLO 训练数据集开始管理素材。</p></div>';
  }

  function renderItems(dataset) {
    const items = dataset.items || [];
    byId("datasetItems").innerHTML = items.length
      ? items.map((item) => {
        const media = item.media_type === "VIDEO"
          ? `<video controls src="${escapeHtml(item.file_url || "")}"></video>`
          : `<img src="${escapeHtml(item.file_url || "")}" alt="${escapeHtml(item.original_name)}">`;
        const split = item.split ? ` · ${escapeHtml(item.split)}` : "";
        const source = ` · ${escapeHtml(sourceLabel(item.source))}`;
        const truthActions = !isTraining(dataset)
          ? `<button class="btn btn-sm ${item.ground_truth === "OK" ? "btn-success" : "btn-outline-success"}" data-item-truth="OK" data-item-id="${item.id}" type="button">OK</button><button class="btn btn-sm ${item.ground_truth === "NG" ? "btn-danger" : "btn-outline-danger"}" data-item-truth="NG" data-item-id="${item.id}" type="button">NG</button>`
          : "";
        const annotationAction = isTraining(dataset)
          ? `<button class="btn btn-sm ${item.annotation_status === "LABELED" ? "btn-outline-success" : "btn-primary"}" data-item-annotate="${item.id}" type="button">${annotationType(dataset) === "DETECTION" ? (item.annotation_status === "LABELED" ? "编辑检测框" : "框选标注") : (item.annotation_status === "LABELED" ? "编辑标注" : "添加标注")}</button>`
          : "";
        const status = isTraining(dataset) ? annotationSummary(item, dataset) : (item.ground_truth || "未标注");
        return `<article class="dataset-item-card"><div>${media}</div><div class="dataset-item-content"><strong title="${escapeHtml(item.original_name)}">${escapeHtml(item.original_name)}</strong><small>${escapeHtml(status)}${split}${source}</small><div class="dataset-item-actions">${truthActions}${annotationAction}<button class="btn btn-sm btn-outline-danger" data-item-remove="${item.id}" type="button">删除</button></div></div></article>`;
      }).join("")
      : '<div class="muted-copy">还没有素材。评测数据集请标记 OK 或 NG；YOLO 训练数据集上传后可直接框选目标。</div>';
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
    const splits = dataset.split_counts || {};
    const summary = [
      ["用途", purposeLabel(dataset)],
      ["标注方式", isTraining(dataset) ? annotationTypeLabel(annotationType(dataset)) : "OK / NG"],
      ["素材数", dataset.item_count || 0],
      [isTraining(dataset) ? "已完成标注" : "已标记真值", markedCount],
      ["训练 / 验证 / 测试", `${splits.TRAIN || 0} / ${splits.VAL || 0} / ${splits.TEST || 0}`],
      ["未切分", splits.UNASSIGNED || 0],
      ["修订", `r${dataset.revision}`],
    ];
    byId("datasetDetailCode").textContent = dataset.code;
    byId("datasetDetailName").textContent = dataset.name;
    byId("datasetDetailMeta").textContent = isTraining(dataset) ? "YOLO 训练" : "场景评测";
    byId("datasetDetailMeta").className = `status-pill ${isTraining(dataset) ? "draft" : "published"}`;
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
    byId("datasetUploadHint").textContent = isTraining(dataset)
      ? "可上传原始图片后在线框选，也可导入包含图片和 labels .txt 的 ZIP 标注包；导入后可直接参与训练。"
      : "上传后为每张图片标记 OK 或 NG，作为场景评测的人工真值。";
    byId("datasetItemsTitle").textContent = isTraining(dataset) ? "训练素材与标注" : "素材与真值";
    byId("datasetItemsHint").textContent = isTraining(dataset)
      ? "训练前会检查类别名称、标注完整度，并自动按 70% / 20% / 10% 切分数据。"
      : "人工 OK / NG 是场景评测准确率的唯一真值来源。";
    renderDatasetLabels(dataset);
    renderCollectionSummary(dataset);
    renderItems(dataset);
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

  function renderDatasetLabels(dataset) {
    const labels = datasetLabels(dataset);
    byId("datasetLabelList").innerHTML = labels.length
      ? labels.map((label) => `<span class="dataset-label-chip">${escapeHtml(label)}<button type="button" data-dataset-label-remove="${escapeHtml(label)}" aria-label="删除 ${escapeHtml(label)}">×</button></span>`).join("")
      : '<span class="muted-copy">尚未维护类别。可在这里先添加，也可在标注时输入新类别。</span>';
    renderLabelOptions();
  }

  function renderCollectionSummary(dataset) {
    const container = byId("datasetCollectionSummary");
    const scene = state.publishedScenarios.find((item) => Number(item.scenario_version_id) === Number(dataset.collection_scenario_version_id));
    if (!dataset.auto_collect_enabled) {
      container.innerHTML = '<span class="muted-copy">未启用自动采集。创建或编辑数据集时可关联一个已发布场景。</span>';
      return;
    }
    container.innerHTML = `<span class="status-pill published">已启用</span><strong>${escapeHtml(scene?.scenario_name || `场景版本 #${dataset.collection_scenario_version_id}`)}</strong><span>达到 ${escapeHtml(dataset.auto_collect_limit || 1000)} 张后自动停止；采集图片需人工标注后才会用于训练或评测。</span>`;
  }

  async function loadPublishedScenarios() {
    try {
      state.publishedScenarios = await request(`${api}/scenarios/published`);
    } catch {
      state.publishedScenarios = [];
    }
    fillCollectionScenarioSelect();
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

  async function loadData(keep = true) {
    state.datasets = await request(`${api}/datasets`);
    if (!keep || !state.datasets.some((item) => item.id === state.selectedId)) {
      state.selectedId = state.datasets[0]?.id ?? null;
    }
    renderList();
    renderDetail();
  }

  async function selectDataset(id) {
    state.selectedId = Number(id);
    const dataset = await request(`${api}/datasets/${state.selectedId}`);
    const index = state.datasets.findIndex((item) => item.id === dataset.id);
    if (index >= 0) state.datasets[index] = dataset;
    renderList();
    renderDetail();
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
    payload.collection_scenario_version_id = Number(byId("datasetCollectionScenario").value) || null;
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
      notify("请选择要上传的素材。", "warning");
      return;
    }
    const data = new FormData();
    files.forEach((file) => data.append("files", file, file.name));
    if (!isTraining(dataset) && byId("datasetUploadTruth").value) data.append("ground_truth", byId("datasetUploadTruth").value);
    try {
      const result = await request(`${api}/datasets/${dataset.id}/items/batch`, { method: "POST", body: data });
      byId("datasetUploadForm").reset();
      byId("datasetFolder").value = "";
      await selectDataset(dataset.id);
      const skipped = result.skipped_count ? `，跳过 ${result.skipped_count} 个重复素材` : "";
      notify(`${isTraining(dataset) ? "训练素材已上传，请点击“框选标注”维护检测框" : "素材已上传，请标记 OK 或 NG"}：${result.created_count} 个${skipped}。`);
    } catch (error) {
      notify(error.message, "danger");
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
    return (dataset?.items || []).filter((item) => item.media_type !== "VIDEO");
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
    state.annotation.viewport.scale = 1;
    state.annotation.viewport.translateX = 0;
    state.annotation.viewport.translateY = 0;
    applyAnnotationViewport();
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
    const values = [["X", box.x], ["Y", box.y], ["宽", box.width], ["高", box.height]];
    byId("annotationSelectedMetrics").innerHTML = values.map(([label, value]) => `<div><small>${label}</small><strong>${(value * 100).toFixed(1)}%</strong></div>`).join("");
  }

  function annotationBoxHandles(box, index) {
    const x = box.x;
    const y = box.y;
    const right = box.x + box.width;
    const bottom = box.y + box.height;
    const centerX = x + (box.width / 2);
    const centerY = y + (box.height / 2);
    const points = { nw: [x, y], n: [centerX, y], ne: [right, y], e: [right, centerY], se: [right, bottom], s: [centerX, bottom], sw: [x, bottom], w: [x, centerY] };
    const size = 0.014;
    return Object.entries(points).map(([handle, [handleX, handleY]]) => `<rect class="annotation-svg-handle" data-box-index="${index}" data-annotation-handle="${handle}" x="${handleX - (size / 2)}" y="${handleY - (size / 2)}" width="${size}" height="${size}"></rect>`).join("");
  }

  function detectionBoxMarkup(box, index, draft = false) {
    const label = escapeHtml(box.label || `目标 ${index + 1}`);
    const selected = !draft && index === state.annotation.selectedBoxIndex;
    const color = draft ? "#f79009" : annotationColor(box.label);
    const handles = selected ? annotationBoxHandles(box, index) : "";
    return `<g class="annotation-svg-object" data-box-index="${index}" style="--annotation-color:${color}"><rect class="annotation-svg-box ${draft ? "annotation-svg-draft" : ""} ${selected ? "selected" : ""}" data-box-index="${index}" x="${box.x}" y="${box.y}" width="${box.width}" height="${box.height}"></rect><text class="annotation-svg-label" x="${Math.min(box.x + 0.006, .92)}" y="${Math.max(box.y + 0.03, .035)}">${label}</text>${handles}</g>`;
  }

  function renderDetectionCanvas() {
    const svg = byId("detectionAnnotationSvg");
    const boxes = state.annotation.boxes;
    svg.innerHTML = boxes.map((box, index) => detectionBoxMarkup(box, index)).join("") + (state.annotation.draft ? detectionBoxMarkup(state.annotation.draft, boxes.length, true) : "");
    const list = byId("detectionAnnotationList");
    list.innerHTML = boxes.length
      ? boxes.map((box, index) => `<div class="annotation-box-row"><button class="annotation-box-select ${index === state.annotation.selectedBoxIndex ? "active" : ""}" style="--annotation-color:${annotationColor(box.label)}" data-box-select="${index}" type="button"><span class="annotation-box-color-dot"></span><span><strong>${escapeHtml(box.label || `目标 ${index + 1}`)}</strong><small>对象 ${index + 1} · ${(box.width * 100).toFixed(1)}% × ${(box.height * 100).toFixed(1)}%</small></span></button><button class="btn btn-sm btn-outline-danger annotation-box-remove" data-box-remove="${index}" type="button" title="删除对象">×</button></div>`).join("")
      : '<div class="annotation-empty">选择“框选”工具，输入类别后拖动鼠标绘制检测框。没有目标的负样本可保持为空。</div>';
    byId("annotationObjectCount").textContent = boxes.length;
    renderAnnotationLabels();
    renderSelectedAnnotationBox();
    updateAnnotationToolUi();
    updateAnnotationNavigation();
  }

  function pointFromEvent(event) {
    const rect = byId("detectionAnnotationSvg").getBoundingClientRect();
    return {
      x: clamp((event.clientX - rect.left) / rect.width),
      y: clamp((event.clientY - rect.top) / rect.height),
    };
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
    return { label: original.label, x: rounded(left), y: rounded(top), width: rounded(right - left), height: rounded(bottom - top) };
  }

  function moveAnnotationBox(original, current) {
    const deltaX = current.x - state.annotation.interaction.start.x;
    const deltaY = current.y - state.annotation.interaction.start.y;
    return {
      label: original.label,
      x: rounded(clamp(original.x + deltaX, 0, 1 - original.width)),
      y: rounded(clamp(original.y + deltaY, 0, 1 - original.height)),
      width: original.width,
      height: original.height,
    };
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
        startClientX: event.clientX,
        startClientY: event.clientY,
        originX: state.annotation.viewport.translateX,
        originY: state.annotation.viewport.translateY,
        moved: false,
      };
      event.currentTarget.setPointerCapture?.(event.pointerId);
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
        start: pointFromEvent(event),
        original: { ...box },
      };
      event.currentTarget.setPointerCapture?.(event.pointerId);
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
    state.annotation.draft = boxFromPoints(state.annotation.start, state.annotation.start, label);
    event.currentTarget.setPointerCapture?.(event.pointerId);
    renderDetectionCanvas();
  }

  function moveDetectionInteraction(event) {
    const interaction = state.annotation.interaction;
    if (interaction?.kind === "PAN") {
      const deltaX = event.clientX - interaction.startClientX;
      const deltaY = event.clientY - interaction.startClientY;
      interaction.moved = interaction.moved || Math.abs(deltaX) > 2 || Math.abs(deltaY) > 2;
      state.annotation.viewport.translateX = interaction.originX + deltaX;
      state.annotation.viewport.translateY = interaction.originY + deltaY;
      applyAnnotationViewport();
      return;
    }
    if (interaction?.kind === "MOVE" || interaction?.kind === "RESIZE") {
      const current = pointFromEvent(event);
      state.annotation.boxes[interaction.index] = interaction.kind === "MOVE"
        ? moveAnnotationBox(interaction.original, current)
        : resizeAnnotationBox(interaction.original, interaction.handle || "se", current);
      setAnnotationDirty();
      renderDetectionCanvas();
      return;
    }
    if (!state.annotation.start) return;
    state.annotation.draft = boxFromPoints(state.annotation.start, pointFromEvent(event), byId("annotationCurrentLabel").value.trim());
    renderDetectionCanvas();
  }

  function finishDetectionInteraction(event) {
    const interaction = state.annotation.interaction;
    if (interaction?.kind === "PAN" && !interaction.moved) {
      state.annotation.selectedBoxIndex = null;
    }
    if (state.annotation.start) {
      const box = boxFromPoints(state.annotation.start, pointFromEvent(event), byId("annotationCurrentLabel").value.trim());
      state.annotation.start = null;
      state.annotation.draft = null;
      if (box.width >= 0.005 && box.height >= 0.005 && box.label) {
        pushAnnotationHistory();
        state.annotation.boxes.push(box);
        state.annotation.selectedBoxIndex = state.annotation.boxes.length - 1;
        setAnnotationDirty();
      }
    }
    state.annotation.interaction = null;
    event.currentTarget.releasePointerCapture?.(event.pointerId);
    renderDetectionCanvas();
  }

  function cancelDetectionDraw() {
    const interaction = state.annotation.interaction;
    if (interaction?.original && Number.isInteger(interaction.index)) {
      state.annotation.boxes[interaction.index] = interaction.original;
    }
    state.annotation.start = null;
    state.annotation.draft = null;
    state.annotation.interaction = null;
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

  function changeAnnotationZoom(step) {
    state.annotation.viewport.scale = clamp(Math.round((state.annotation.viewport.scale + step) * 100) / 100, 0.35, 4);
    applyAnnotationViewport();
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
    const labels = datasetLabels(dataset);
    byId("annotationCurrentLabel").value = item.annotation_json?.boxes?.[0]?.label || labels[0] || "";
    byId("classificationAnnotationLabel").value = item.annotation_json?.label || labels[0] || "";
    renderLabelOptions();
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
      annotationJson = { boxes: state.annotation.boxes.map((box) => ({ label: box.label.trim(), x: rounded(box.x), y: rounded(box.y), width: rounded(box.width), height: rounded(box.height) })) };
    } else if (type === "CLASSIFICATION") {
      const label = byId("classificationAnnotationLabel").value.trim();
      if (!label) {
        notify("请填写图片类别名称。", "warning");
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
    try {
      await request(`${api}/datasets/${dataset.id}/items/${itemId}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ annotation_status: "LABELED", annotation_json: annotationJson }),
      });
      setAnnotationDirty(false);
      closeModal("datasetAnnotationModal");
      await selectDataset(dataset.id);
      notify("训练标注已保存。");
    } catch (error) {
      notify(error.message, "danger");
    }
  }

  async function removeItem(itemId) {
    const dataset = selected();
    if (!dataset || !confirm("删除后该素材不会再参与训练或评测。确认删除吗？")) return;
    try {
      await request(`${api}/datasets/${dataset.id}/items/${itemId}`, { method: "DELETE" });
      await selectDataset(dataset.id);
      notify("素材已删除。");
    } catch (error) {
      notify(error.message, "danger");
    }
  }

  document.addEventListener("DOMContentLoaded", () => {
    byId("openDatasetCreate").addEventListener("click", openDatasetCreate);
    byId("datasetPurpose").addEventListener("change", syncDatasetCreateMode);
    byId("createDataset").addEventListener("click", createDataset);
    byId("datasetCreateForm").addEventListener("submit", (event) => {
      event.preventDefault();
      createDataset();
    });
    byId("reloadDatasets").addEventListener("click", () => loadData().catch((error) => notify(error.message, "danger")));
    byId("datasetSearch").addEventListener("input", renderList);
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
    });
    byId("datasetFile").addEventListener("change", () => {
      if (byId("datasetFile").files.length) {
        byId("datasetFolder").value = "";
        byId("offlineYoloArchive").value = "";
      }
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
      changeAnnotationZoom(event.deltaY < 0 ? 0.12 : -0.12);
    }, { passive: false });
    byId("detectionAnnotationSvg").addEventListener("pointerdown", startDetectionInteraction);
    byId("detectionAnnotationSvg").addEventListener("pointermove", moveDetectionInteraction);
    byId("detectionAnnotationSvg").addEventListener("pointerup", finishDetectionInteraction);
    byId("detectionAnnotationSvg").addEventListener("pointercancel", cancelDetectionDraw);
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
    byId("annotationSelectedLabel").addEventListener("change", (event) => {
      const box = selectedAnnotationBox();
      const label = event.target.value.trim();
      if (!box || !label || box.label === label) return;
      pushAnnotationHistory();
      box.label = label;
      byId("annotationCurrentLabel").value = label;
      setAnnotationDirty();
      renderDetectionCanvas();
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
      const truthButton = event.target.closest("[data-item-truth]");
      const annotateButton = event.target.closest("[data-item-annotate]");
      const removeButton = event.target.closest("[data-item-remove]");
      if (truthButton) updateTruth(truthButton.dataset.itemId, truthButton.dataset.itemTruth);
      else if (annotateButton) openAnnotation(annotateButton.dataset.itemAnnotate);
      else if (removeButton) removeItem(removeButton.dataset.itemRemove);
    });
    syncDatasetCreateMode();
    Promise.all([loadData(false), loadPublishedScenarios()]).catch((error) => notify(error.message, "danger"));
  });
})();
