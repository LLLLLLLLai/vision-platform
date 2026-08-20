const api = "/api/v1";
const state = {
  products: [],
  stations: [],
  recipes: [],
  references: [],
  referenceObjectTypes: [],
  referenceCandidates: [],
  details: new Map(),
  recipe: null,
  worldScene: null,
  selectedRoiId: null,
  discoveryCandidates: [],
  harnessSegments: [],
  harnessSegmentation: null,
  discoveryEngine: null,
  selectedCandidateId: null,
  discovering: false,
  pendingRect: null,
  drawing: false,
  startPoint: null,
  pointerCandidateRoi: null,
  interactionMode: null,
  workingRect: null,
  originalRect: null,
  savingRoi: false,
  draftRules: [],
  vlmPromptDirty: false,
  detectionRecords: [],
  libraryPage: 1,
  libraryPageSize: 12,
  modelServices: [],
  activeModelServiceLogCode: null,
  modelServiceLogTimer: null,
  testRecipe: null,
  testFile: null,
  testDraft: null,
  imageView: {
    scale: 1,
    minScale: 0.2,
    maxScale: 6,
    translateX: 0,
    translateY: 0,
    displayWidth: 0,
    displayHeight: 0,
    panMode: false,
    panning: false,
    panStartX: 0,
    panStartY: 0,
    panOriginX: 0,
    panOriginY: 0,
    spacePressed: false,
    resetOnLoad: true,
  },
};

const byId = (id) => document.getElementById(id);
const canvas = byId("roiCanvas");
const context = canvas.getContext("2d");
const baseImage = byId("baseImage");
const imageStage = byId("imageStage");
const imageSurface = byId("imageTransformSurface");

async function request(url, options = {}) {
  const response = await fetch(url, options);
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.detail || payload.message || "请求失败");
  return payload;
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function notify(message, type = "success", scrollToTop = true) {
  byId("workspaceAlert").innerHTML =
    `<div class="alert alert-${type} alert-dismissible fade show">${escapeHtml(message)}` +
    '<button type="button" class="btn-close" data-bs-dismiss="alert"></button></div>';
  if (scrollToTop) window.scrollTo({ top: 0, behavior: "smooth" });
}

function formValues(form) {
  return Object.fromEntries(new FormData(form).entries());
}

function normalizeCode(value) {
  return String(value || "")
    .trim()
    .toUpperCase()
    .replace(/[^A-Z0-9_-]+/g, "_")
    .replace(/^_+|_+$/g, "");
}

function recipeFields() {
  const values = formValues(byId("recipeForm"));
  return {
    lineCode: normalizeCode(values.line_code),
    materialCode: normalizeCode(values.material_code),
    processCode: normalizeCode(values.process_code),
    cameraCode: normalizeCode(values.camera_code),
    captureIndex: Math.max(1, Number(values.capture_index || 1)),
    version: values.version.trim() || "1.0",
  };
}

function generatedRecipe(fields = recipeFields()) {
  const parts = [
    fields.lineCode,
    fields.materialCode,
    fields.processCode,
    fields.cameraCode,
    fields.captureIndex ? `P${fields.captureIndex}` : "",
  ].filter(Boolean);
  return {
    code: parts.join("-"),
    name: parts.length === 5
      ? `${fields.lineCode} · ${fields.materialCode} · ${fields.processCode} · ${fields.cameraCode} · 第${fields.captureIndex}次拍照`
      : "请填写上方配方信息",
  };
}

function updateGeneratedName() {
  byId("generatedRecipeName").textContent = generatedRecipe().name;
}

async function loadData(preferredRecipeId = null) {
  [
    state.products,
    state.stations,
    state.recipes,
    state.references,
    state.referenceObjectTypes,
  ] = await Promise.all([
    request(`${api}/configuration/products`),
    request(`${api}/configuration/stations`),
    request(`${api}/configuration/recipes`),
    request(`${api}/configuration/reference-groups`),
    request(`${api}/configuration/reference-object-types`),
  ]);
  const entries = await Promise.all(
    state.recipes.map(async (recipe) => [
      recipe.id,
      await request(`${api}/configuration/recipes/${recipe.id}`),
    ]),
  );
  state.details = new Map(entries);
  fillReferenceSelects();
  fillObjectTypeSelect();
  populateLibraryFilters();
  renderLibrary();
  renderReferenceLibrary();
  if (preferredRecipeId) await loadRecipe(preferredRecipeId);
}

function fillObjectTypeSelect(selectedCode = null) {
  const select = byId("roiObjectType");
  const current = selectedCode || select.value || "OBJECT";
  select.innerHTML = state.referenceObjectTypes.length
    ? state.referenceObjectTypes.map((item) => `
        <option value="${escapeHtml(item.code)}" ${item.code === current ? "selected" : ""}>
          ${escapeHtml(item.name)} (${escapeHtml(item.code)})
        </option>`).join("")
    : '<option value="">请先在视觉标准库创建物体类型</option>';
  select.disabled = !state.referenceObjectTypes.length;
  if (state.referenceObjectTypes.length && !select.value) {
    select.value = state.referenceObjectTypes[0].code;
  }
  const referenceSelect = byId("newReferenceObjectType");
  if (!referenceSelect) return;
  const referenceCurrent = referenceSelect.value || state.referenceObjectTypes[0]?.code || "";
  referenceSelect.innerHTML = state.referenceObjectTypes.length
    ? state.referenceObjectTypes.map((item) => `
        <option value="${escapeHtml(item.code)}" ${item.code === referenceCurrent ? "selected" : ""}>
          ${escapeHtml(item.name)} (${escapeHtml(item.code)})
        </option>`).join("")
    : '<option value="">请先新建物体类型</option>';
  referenceSelect.disabled = !state.referenceObjectTypes.length;
}

function fillReferenceSelects(selectedId = null) {
  document.querySelectorAll(".reference-group-select").forEach((select) => {
    const current = selectedId || select.value;
    select.innerHTML = state.references.length
      ? state.references.map((item) => `
          <option value="${item.id}" ${String(item.id) === String(current) ? "selected" : ""}>
            ${escapeHtml(item.code)} · ${escapeHtml(item.name)} (${item.image_count} 张)
          </option>`).join("")
      : '<option value="">请先创建参考类别</option>';
  });
}

async function reloadReferenceLibrary() {
  state.referenceCandidates = await request(`${api}/reference-candidates?limit=200`);
  renderReferenceCandidates();
}

function candidateStatusMeta(status) {
  const values = {
    ACCEPTED: ["VLM已通过", "accepted"],
    UNCERTAIN: ["需要确认", "uncertain"],
    REJECTED: ["已拒绝", "rejected"],
    ERROR: ["复核失败", "error"],
    PROMOTED: ["已加入正式基准", "promoted"],
    SKIPPED: ["无需加入", "skipped"],
    PENDING_VLM: ["等待VLM复核", "pending"],
  };
  return values[status] || [status || "未知", "pending"];
}

function renderReferenceCandidates() {
  const container = byId("referenceCandidateGrid");
  if (!container) return;
  const statusFilter = byId("referenceCandidateStatusFilter")?.value || "";
  const candidates = state.referenceCandidates.filter(
    (candidate) => !statusFilter || candidate.status === statusFilter,
  );
  container.innerHTML = candidates.length
    ? candidates.map((candidate) => {
        const [statusLabel, statusClass] = candidateStatusMeta(candidate.status);
        const parsed = candidate.vlm_result?.parsed || {};
        const differences = Array.isArray(parsed.differences) && parsed.differences.length
          ? parsed.differences.join("；")
          : "未发现关键差异";
        const canPromote = ["ACCEPTED", "UNCERTAIN"].includes(candidate.status)
          && !candidate.promoted_reference_image_id;
        const canReject = !["PROMOTED", "REJECTED", "SKIPPED"].includes(candidate.status);
        return `
          <article class="reference-candidate-card" data-candidate-id="${candidate.id}">
            <header>
              <div>
                <strong>${escapeHtml(candidate.roi_name || candidate.roi_code || "检测区域")}</strong>
                <small>${escapeHtml(candidate.recipe_name || candidate.recipe_code || "未知配方")} · SN ${escapeHtml(candidate.sn)}</small>
              </div>
              <span class="candidate-status ${statusClass}">${escapeHtml(statusLabel)}</span>
            </header>
            <div class="candidate-image-comparison">
              <figure>
                <figcaption>合格基准</figcaption>
                ${candidate.baseline_image_url
                  ? `<img src="${escapeHtml(candidate.baseline_image_url)}?v=${encodeURIComponent(candidate.created_at)}" alt="合格基准">`
                  : '<div class="candidate-image-empty">基准图不可预览</div>'}
              </figure>
              <figure>
                <figcaption>本次检测ROI</figcaption>
                ${candidate.candidate_image_url
                  ? `<img src="${escapeHtml(candidate.candidate_image_url)}?v=${encodeURIComponent(candidate.created_at)}" alt="本次检测ROI">`
                  : '<div class="candidate-image-empty">候选图不可预览</div>'}
              </figure>
            </div>
            <div class="candidate-evidence">
              <span><small>主模型相似度</small><strong>${candidate.similarity_score == null ? "—" : Number(candidate.similarity_score).toFixed(4)}</strong></span>
              <span><small>VLM置信度</small><strong>${candidate.vlm_confidence == null ? "—" : Number(candidate.vlm_confidence).toFixed(2)}</strong></span>
              <span><small>图片质量</small><strong>${candidate.quality?.passed ? "通过" : "未通过"}</strong></span>
              <span><small>正式基准</small><strong>${Number(candidate.active_reference_count || 0)} / ${Number(candidate.reference_limit || 10)}</strong></span>
            </div>
            <div class="candidate-review-reason">
              <strong>VLM双图结论</strong>
              <p>${escapeHtml(candidate.reason || "等待复核")}</p>
              <small>${escapeHtml(differences)}</small>
            </div>
            <footer>
              ${canReject ? `<button class="btn btn-sm btn-outline-danger" type="button" data-candidate-reject="${candidate.id}">拒绝</button>` : ""}
              ${canPromote ? `<button class="btn btn-sm btn-primary" type="button" data-candidate-promote="${candidate.id}">加入正式基准</button>` : ""}
            </footer>
          </article>`;
      }).join("")
    : '<div class="reference-library-empty wide">当前筛选条件下没有候选图片。</div>';
}

async function updateReferenceCandidate(candidateId, action) {
  const result = await request(`${api}/reference-candidates/${candidateId}/${action}`, { method: "POST" });
  await reloadReferenceLibrary();
  return result;
}

function renderReferenceLibrary() {
  const typeContainer = byId("referenceObjectTypeList");
  const groupContainer = byId("referenceGroupGrid");
  if (!typeContainer || !groupContainer) return;
  const query = byId("referenceLibrarySearch")?.value.trim().toLowerCase() || "";
  const matchesQuery = (...values) => !query || values.some(
    (value) => String(value || "").toLowerCase().includes(query),
  );
  const visibleTypes = state.referenceObjectTypes.filter((item) =>
    matchesQuery(item.code, item.name, item.description));
  const visibleGroups = state.references.filter((item) =>
    matchesQuery(item.code, item.name, item.object_type, item.class_code));
  const totalImages = state.references.reduce((sum, group) => sum + Number(group.image_count || 0), 0);
  byId("referenceLibrarySummary").innerHTML = `
    <span><small>物体类型</small><strong>${state.referenceObjectTypes.length}</strong></span>
    <span><small>标准类别</small><strong>${state.references.length}</strong></span>
    <span><small>正式标准图</small><strong>${totalImages}</strong></span>
    <span><small>当前筛选</small><strong>${visibleGroups.length} 类</strong></span>`;
  typeContainer.innerHTML = visibleTypes.length
    ? visibleTypes.map((item) => {
        const groups = state.references.filter((group) => group.object_type === item.code);
        const imageCount = groups.reduce((sum, group) => sum + Number(group.image_count || 0), 0);
        return `
          <article class="reference-object-type-card">
            <span>${escapeHtml(item.code.slice(0, 2))}</span>
            <div><strong>${escapeHtml(item.name)}</strong><small>${escapeHtml(item.code)}</small></div>
            <div class="reference-type-count"><b>${groups.length}</b><small>类别</small></div>
            <div class="reference-type-count"><b>${imageCount}</b><small>图片</small></div>
          </article>`;
      }).join("")
    : '<div class="reference-library-empty">没有匹配的物体类型</div>';
  groupContainer.innerHTML = visibleGroups.length
    ? visibleGroups.map((group) => {
        const type = state.referenceObjectTypes.find((item) => item.code === group.object_type);
        const images = group.images || [];
        return `
          <article class="reference-group-card" data-reference-group-id="${group.id}">
            <header>
              <div>
                <small>${escapeHtml(type?.name || group.object_type)} · ${escapeHtml(group.class_code)}</small>
                <strong>${escapeHtml(group.name)}</strong>
                <code>${escapeHtml(group.code)}</code>
              </div>
              <span class="reference-image-count">${images.length} 张</span>
            </header>
            <div class="reference-image-grid">
              ${images.map((image) => `
                <figure class="reference-image-card" data-reference-image-id="${image.id}">
                  <img src="${escapeHtml(image.image_url)}?v=${encodeURIComponent(image.created_at || image.id)}" alt="${escapeHtml(group.name)}标准图">
                  <figcaption>
                    <span class="embedding-status ${String(image.quality_status || "PENDING").toLowerCase()}">${escapeHtml(image.quality_status || "PENDING")}</span>
                    <button type="button" class="remove-reference-image" title="移出当前图库">删除</button>
                  </figcaption>
                </figure>`).join("") || '<div class="reference-image-empty">暂时没有标准图，可从当前ROI添加或在这里上传。</div>'}
            </div>
            <footer>
              <label class="btn btn-sm btn-outline-primary reference-upload-button">
                <input type="file" accept="image/*" multiple hidden data-reference-upload="${group.id}">
                ＋ 上传标准图片
              </label>
              <small>支持多选；上传后自动生成DINOv2特征向量</small>
            </footer>
          </article>`;
      }).join("")
    : '<div class="reference-library-empty wide">没有匹配的标准类别，请新建类别后上传标准图片。</div>';
}

async function createObjectTypeFromLibrary() {
  const code = normalizeCode(byId("newObjectTypeCode").value);
  const name = byId("newObjectTypeName").value.trim();
  if (!code || !name) {
    notify("请填写物体类型编码和名称", "warning", false);
    return;
  }
  await request(`${api}/configuration/reference-object-types`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ code, name }),
  });
  byId("newObjectTypeCode").value = "";
  byId("newObjectTypeName").value = "";
  await reloadReferenceLibrary();
  notify(`物体类型“${name}”已加入视觉标准库`, "success", false);
}

async function createReferenceGroupFromLibrary() {
  const code = normalizeCode(byId("newReferenceCode").value);
  const name = byId("newReferenceName").value.trim();
  const objectType = byId("newReferenceObjectType").value;
  const classCode = normalizeCode(byId("newReferenceClassCode").value);
  if (!code || !name || !objectType || !classCode) {
    notify("请完整填写标准类别编码、名称、物体类型和判定类别", "warning", false);
    return;
  }
  await request(`${api}/configuration/reference-groups`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      code,
      name,
      object_type: objectType,
      class_code: classCode,
      description: "视觉标准库手动创建",
    }),
  });
  byId("newReferenceCode").value = "";
  byId("newReferenceName").value = "";
  byId("newReferenceClassCode").value = "";
  await reloadReferenceLibrary();
  notify(`标准类别“${name}”已创建，可以继续上传标准图片`, "success", false);
}

async function uploadReferenceFiles(groupId, files) {
  if (!files.length) return;
  let failed = 0;
  for (const file of files) {
    const data = new FormData();
    data.append("file", file);
    try {
      await request(`${api}/configuration/reference-groups/${groupId}/images`, {
        method: "POST",
        body: data,
      });
    } catch (_error) {
      failed += 1;
    }
  }
  await reloadReferenceLibrary();
  notify(
    failed ? `${files.length - failed}张图片上传成功，${failed}张处理失败` : `${files.length}张标准图片已上传并完成处理`,
    failed ? "warning" : "success",
    false,
  );
}

function populateRecipeForm(recipe) {
  const form = byId("recipeForm");
  form.elements.line_code.value = recipe?.line_code || "";
  form.elements.material_code.value = recipe?.material_code || "";
  form.elements.process_code.value = recipe?.process_code || "";
  form.elements.camera_code.value = recipe?.camera_code || "";
  form.elements.capture_index.value = recipe?.capture_index || 1;
  form.elements.version.value = recipe?.version || "1.0";
  updateGeneratedName();
}

function setRecipeStatus(status, recipe = null) {
  const isPublished = status === "PUBLISHED";
  byId("recipeStatus").className = `status-pill ${isPublished ? "published" : "draft"}`;
  byId("recipeStatus").innerHTML = `
    <span class="status-dot ${isPublished ? "published" : "draft"}"></span>
    ${isPublished ? "已保存到视觉库" : recipe ? "编辑中" : "未保存"}`;
}

function resetEditor() {
  state.recipe = null;
  state.worldScene = null;
  state.selectedRoiId = null;
  state.discoveryCandidates = [];
  state.harnessSegments = [];
  state.harnessSegmentation = null;
  state.discoveryEngine = null;
  state.selectedCandidateId = null;
  state.pendingRect = null;
  state.workingRect = null;
  state.interactionMode = null;
  byId("recipeForm").reset();
  byId("recipeForm").elements.capture_index.value = "1";
  byId("recipeForm").elements.version.value = "1.0";
  populateRecipeForm(null);
  setRecipeStatus("DRAFT");
  baseImage.removeAttribute("src");
  baseImage.removeAttribute("data-source-url");
  baseImage.style.display = "none";
  imageSurface.hidden = true;
  byId("imageNavigationHint").hidden = true;
  byId("emptyStage").style.display = "grid";
  byId("autoDiscoverButton").disabled = true;
  renderDiscoveryCandidates();
  clearCanvas();
  renderConfiguredObjects();
}

async function loadRecipe(recipeId) {
  const recipeUrl = `${api}/configuration/recipes/${recipeId}`;
  state.recipe = await request(recipeUrl);
  state.worldScene = await request(`${api}/world/recipes/${recipeId}/scene`);
  state.recipe = await request(recipeUrl);
  state.details.set(state.recipe.id, state.recipe);
  state.selectedRoiId = null;
  state.discoveryCandidates = [];
  state.harnessSegments = [];
  state.harnessSegmentation = null;
  state.discoveryEngine = null;
  state.selectedCandidateId = null;
  state.pendingRect = null;
  state.workingRect = null;
  state.interactionMode = null;
  populateRecipeForm(state.recipe);
  setRecipeStatus(state.recipe.status, state.recipe);
  if (state.recipe.base_image_url) {
    const imageChanged = baseImage.dataset.sourceUrl !== state.recipe.base_image_url;
    if (imageChanged) {
      state.imageView.resetOnLoad = true;
      baseImage.dataset.sourceUrl = state.recipe.base_image_url;
      baseImage.src = `${state.recipe.base_image_url}?v=${Date.now()}`;
    }
    baseImage.style.display = "block";
    imageSurface.hidden = false;
    byId("imageNavigationHint").hidden = false;
    byId("emptyStage").style.display = "none";
    byId("autoDiscoverButton").disabled = false;
    if (!imageChanged && baseImage.complete && baseImage.naturalWidth) syncCanvas(false);
  } else {
    baseImage.removeAttribute("src");
    baseImage.removeAttribute("data-source-url");
    baseImage.style.display = "none";
    imageSurface.hidden = true;
    byId("imageNavigationHint").hidden = true;
    byId("emptyStage").style.display = "grid";
    byId("autoDiscoverButton").disabled = true;
    clearCanvas();
  }
  renderDiscoveryCandidates();
  renderConfiguredObjects();
}

async function ensureProduct(code) {
  const existing = state.products.find((item) => item.code === code);
  if (existing) return existing;
  const created = await request(`${api}/configuration/products`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ code, name: code }),
  });
  state.products.push(created);
  return created;
}

async function ensureStation(lineCode, processCode) {
  const code = `${lineCode}_${processCode}`;
  const existing = state.stations.find((item) => item.code === code);
  if (existing) return existing;
  const created = await request(`${api}/configuration/stations`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      code,
      name: `${lineCode} · ${processCode}`,
      line_code: lineCode,
      process_code: processCode,
    }),
  });
  state.stations.push({ ...created, line_code: lineCode, process_code: processCode });
  return created;
}

async function ensureWorkingRecipe() {
  const fields = recipeFields();
  if (!fields.lineCode || !fields.materialCode || !fields.processCode || !fields.cameraCode) {
    throw new Error("请先填写拉线、物料、工序和相机信息");
  }
  const generated = generatedRecipe(fields);
  const product = await ensureProduct(fields.materialCode);
  const station = await ensureStation(fields.lineCode, fields.processCode);
  const payload = {
    code: generated.code,
    name: generated.name,
    version: fields.version,
    product_id: product.id,
    station_id: station.id,
    camera_code: fields.cameraCode,
    capture_index: fields.captureIndex,
  };
  let recipeId = state.recipe?.id;
  if (recipeId) {
    await request(`${api}/configuration/recipes/${recipeId}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
  } else {
    const existing = state.recipes.find((item) => item.code === generated.code);
    if (existing) {
      recipeId = existing.id;
      await request(`${api}/configuration/recipes/${recipeId}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
    } else {
      const created = await request(`${api}/configuration/recipes`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      recipeId = created.id;
    }
  }
  await loadData(recipeId);
  return state.recipe;
}

async function saveRecipe() {
  try {
    const recipe = await ensureWorkingRecipe();
    if (!recipe.base_image_url || !recipe.rois.length || !recipe.rois.some((roi) => roi.inspection_items.length)) {
      notify("配方草稿已保存。请至少上传图片、框选一个检测物体并添加一条规则后再保存到视觉库。", "warning");
      return;
    }
    await request(`${api}/configuration/recipes/${recipe.id}/publish`, { method: "POST" });
    await loadData(recipe.id);
    notify("规则配方已保存到视觉库，第三方系统可以按配置编码调用检测接口");
  } catch (error) {
    notify(error.message, "danger");
  }
}

async function uploadBaseImage(file) {
  if (!file) return;
  const replacingConfiguredImage = Boolean(
    state.recipe?.base_image_url && state.recipe.rois?.length,
  );
  if (
    replacingConfiguredImage
    && !window.confirm("更换图片会清除当前配方的全部 ROI、校验规则和自动参考图，是否继续？")
  ) {
    byId("baseImageInput").value = "";
    byId("emptyImageInput").value = "";
    return;
  }
  try {
    const recipe = await ensureWorkingRecipe();
    const data = new FormData();
    data.append("file", file);
    const result = await request(`${api}/configuration/recipes/${recipe.id}/image`, {
      method: "POST",
      body: data,
    });
    byId("baseImageInput").value = "";
    byId("emptyImageInput").value = "";
    baseImage.removeAttribute("data-source-url");
    state.imageView.resetOnLoad = true;
    await loadRecipe(recipe.id);
    notify("图片上传成功。可直接手动画框，或点击“自动解析”生成 AI 候选物体。", "success", false);
  } catch (error) {
    notify(error.message, "danger");
  }
}

async function discoverObjects() {
  if (!state.recipe?.base_image_url || state.discovering) return;
  state.discovering = true;
  state.discoveryCandidates = [];
  state.harnessSegments = [];
  state.harnessSegmentation = null;
  state.discoveryEngine = null;
  state.selectedCandidateId = null;
  byId("discoveryOverlay").hidden = false;
  byId("autoDiscoverButton").disabled = true;
  renderDiscoveryCandidates();
  drawCanvas();
  try {
    const result = await request(`${api}/algorithms/discover`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        recipe_id: state.recipe.id,
        object_types: [
          "fuse", "screw", "connector", "wiring harness connection",
          "PCBA", "busbar", "relay", "terminal", "label",
        ],
        max_objects: 60,
      }),
    });
    const allCandidates = [
      ...(result.candidates || []),
      ...(result.segmentation_candidates || []),
    ];
    state.discoveryCandidates = allCandidates.map((candidate) => ({
      ...candidate,
      selected: true,
    }));
    state.harnessSegmentation = result.harness_segmentation || null;
    state.harnessSegments = result.harness_segmentation?.segments || [];
    state.discoveryEngine = result.engine || "QWEN3_VL + GROUNDING_DINO";
    state.selectedCandidateId = state.discoveryCandidates[0]?.candidate_id || null;
    renderDiscoveryCandidates();
    drawCanvas();
    notify(
      state.discoveryCandidates.length
        ? `自动解析完成：生成 ${state.discoveryCandidates.length} 个候选物体，其中包含 ${state.harnessSegments.length} 段线束分割坐标。请检查后确认。`
        : "自动解析未找到可靠候选物体，你仍可直接在图片上手动画框。",
      state.discoveryCandidates.length ? "success" : "warning",
      false,
    );
  } catch (error) {
    state.discoveryCandidates = [];
    state.harnessSegments = [];
    state.harnessSegmentation = null;
    state.discoveryEngine = null;
    renderDiscoveryCandidates();
    drawCanvas();
    notify(`${error.message}。当前仍可直接在图片上手动画框。`, "warning", false);
  } finally {
    state.discovering = false;
    byId("discoveryOverlay").hidden = true;
    byId("autoDiscoverButton").disabled = false;
  }
}

function candidateRect(candidate) {
  return {
    x: candidate.x_ratio * canvas.width,
    y: candidate.y_ratio * canvas.height,
    width: candidate.width_ratio * canvas.width,
    height: candidate.height_ratio * canvas.height,
  };
}

function selectedCandidate() {
  return state.discoveryCandidates.find(
    (candidate) => candidate.candidate_id === state.selectedCandidateId,
  ) || null;
}

function selectCandidate(candidateId) {
  state.selectedCandidateId = candidateId;
  state.selectedRoiId = null;
  state.pendingRect = null;
  state.workingRect = null;
  renderDiscoveryCandidates();
  renderConfiguredObjects();
  drawCanvas();
}

function deleteDiscoveryCandidate(candidateId) {
  const candidate = state.discoveryCandidates.find((item) => item.candidate_id === candidateId);
  if (!candidate) return;
  state.discoveryCandidates = state.discoveryCandidates.filter(
    (item) => item.candidate_id !== candidateId,
  );
  if (candidate.source_segment_id) {
    state.harnessSegments = state.harnessSegments.filter(
      (segment) => segment.segment_id !== candidate.source_segment_id,
    );
    if (state.harnessSegmentation?.segments) {
      state.harnessSegmentation.segments = state.harnessSegmentation.segments.filter(
        (segment) => segment.segment_id !== candidate.source_segment_id,
      );
    }
  }
  if (state.selectedCandidateId === candidateId) {
    state.selectedCandidateId = state.discoveryCandidates[0]?.candidate_id || null;
    state.workingRect = null;
    state.originalRect = null;
    state.pointerCandidateRoi = null;
    state.interactionMode = null;
  }
  renderDiscoveryCandidates();
  drawCanvas();
  notify(`候选框“${candidate.label || candidateId}”已删除`, "info", false);
}

function renderDiscoveryCandidates() {
  const candidates = state.discoveryCandidates;
  byId("candidateObjectSection").hidden = !candidates.length && !state.harnessSegments.length;
  const segmentSummary = byId("harnessSegmentSummary");
  segmentSummary.hidden = !state.harnessSegments.length;
  const supportedScope = state.harnessSegmentation?.supported_scope || "线束";
  byId("harnessSegmentCount").textContent = state.harnessSegments.length
    ? `${state.harnessSegments.length} 段 · ${supportedScope} · 已叠加像素级轮廓`
    : "未分割到线束";
  byId("candidateEngineBadge").textContent = state.discoveryEngine
    ? "Grounding + SAM2 定位结果"
    : "等待定位";
  byId("candidateObjectList").innerHTML = candidates.map((candidate) => {
    const isSegment = candidate.target_kind === "HARNESS_SEGMENT";
    const recommended = candidate.batch_confirmable !== false && (
      candidate.review_status === "RECOMMENDED"
      || (candidate.confidence || 0) >= 0.40
    );
    const source = isSegment
      ? (candidate.engine === "SAM2.1_HIERA_SMALL" ? "SAM2 像素分割" : "OpenCV 颜色分割")
      : "Grounding DINO 定位";
    const coordinates = `X ${Number(candidate.x_ratio || 0).toFixed(3)} · Y ${Number(candidate.y_ratio || 0).toFixed(3)} · W ${Number(candidate.width_ratio || 0).toFixed(3)} · H ${Number(candidate.height_ratio || 0).toFixed(3)}`;
    return `
    <article class="candidate-object-card ${candidate.candidate_id === state.selectedCandidateId ? "selected" : ""}"
      data-candidate-id="${escapeHtml(candidate.candidate_id)}">
      <div>
        <strong>${escapeHtml(candidate.label)}</strong>
        <small>${escapeHtml(candidate.object_type)} · ${escapeHtml(source)} · ${escapeHtml(coordinates)}</small>
      </div>
      <span class="candidate-confidence ${recommended ? "recommended" : "review-required"}">
        ${recommended ? "建议确认" : "待人工复核"} · ${isSegment ? "分割" : "定位"} ${Math.round((candidate.confidence || 0) * 100)}%
      </span>
      <div class="candidate-card-actions">
        <button class="btn btn-sm btn-outline-primary confirm-candidate" type="button">确认为检测对象</button>
        <button class="btn btn-sm btn-outline-danger delete-candidate" type="button" title="删除此候选框">删除</button>
      </div>
    </article>`;
  }).join("");
}

function candidateCode(candidate, usedCodes) {
  const prefix = normalizeCode(candidate.object_type || "OBJECT") || "OBJECT";
  let index = 1;
  let code = `${prefix}_${String(index).padStart(2, "0")}`;
  while (usedCodes.has(code)) {
    index += 1;
    code = `${prefix}_${String(index).padStart(2, "0")}`;
  }
  usedCodes.add(code);
  return code;
}

async function confirmCandidates(candidates) {
  if (!candidates.length || state.savingRoi) return;
  state.savingRoi = true;
  const createdIds = [];
  const usedCodes = new Set((state.recipe?.rois || []).map((roi) => roi.code));
  try {
    for (const [index, candidate] of candidates.entries()) {
      const created = await request(`${api}/configuration/recipes/${state.recipe.id}/rois`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          code: candidateCode(candidate, usedCodes),
          name: candidate.label,
          object_type: candidate.object_type || "OBJECT",
          padding: 0,
          sort_order: state.recipe.rois.length + index,
          x_ratio: candidate.x_ratio,
          y_ratio: candidate.y_ratio,
          width_ratio: candidate.width_ratio,
          height_ratio: candidate.height_ratio,
        }),
      });
      createdIds.push(created.id);
      try {
        await request(`${api}/configuration/rois/${created.id}/capture-reference`, {
          method: "POST",
        });
      } catch (_error) {
        // The ROI remains usable even if its reference crop is generated later.
      }
    }
    const confirmedIds = new Set(candidates.map((candidate) => candidate.candidate_id));
    const remainingCandidates = state.discoveryCandidates.filter(
      (candidate) => !confirmedIds.has(candidate.candidate_id),
    );
    await loadRecipe(state.recipe.id);
    await waitForBaseImage();
    state.discoveryCandidates = remainingCandidates;
    state.selectedCandidateId = remainingCandidates[0]?.candidate_id || null;
    renderDiscoveryCandidates();
    drawCanvas();
    if (createdIds.length === 1) openObjectModal(createdIds[0]);
    notify(`已确认 ${createdIds.length} 个候选物体，检测框已写入产品世界模型。`, "success", false);
  } catch (error) {
    notify(error.message, "danger", false);
  } finally {
    state.savingRoi = false;
  }
}

function applyImageTransform() {
  const view = state.imageView;
  imageSurface.style.transform =
    `translate(${view.translateX}px, ${view.translateY}px) scale(${view.scale})`;
  const zoomIndicator = byId("resetZoomButton");
  const panButton = byId("togglePanButton");
  if (zoomIndicator) zoomIndicator.textContent = `${Math.round(view.scale * 100)}%`;
  if (panButton) panButton.classList.toggle("active", view.panMode);
  canvas.classList.toggle("pan-mode", view.panMode || view.spacePressed);
  canvas.classList.toggle("panning", view.panning);
}

function resetImageView() {
  const view = state.imageView;
  view.scale = 1;
  view.translateX = Math.max(0, (imageStage.clientWidth - view.displayWidth) / 2);
  view.translateY = Math.max(0, (imageStage.clientHeight - view.displayHeight) / 2);
  applyImageTransform();
}

function syncCanvas(resetView = true) {
  if (!baseImage.naturalWidth || !baseImage.naturalHeight) return;
  const availableWidth = Math.max(320, imageStage.clientWidth - 32);
  const availableHeight = Math.max(320, imageStage.clientHeight - 32);
  const fitScale = Math.min(
    availableWidth / baseImage.naturalWidth,
    availableHeight / baseImage.naturalHeight,
    1,
  );
  const width = Math.max(1, Math.round(baseImage.naturalWidth * fitScale));
  const height = Math.max(1, Math.round(baseImage.naturalHeight * fitScale));
  state.imageView.displayWidth = width;
  state.imageView.displayHeight = height;
  imageSurface.style.width = `${width}px`;
  imageSurface.style.height = `${height}px`;
  baseImage.style.width = `${width}px`;
  baseImage.style.height = `${height}px`;
  canvas.width = width;
  canvas.height = height;
  canvas.style.width = `${width}px`;
  canvas.style.height = `${height}px`;
  if (resetView) resetImageView();
  else applyImageTransform();
  drawCanvas();
}

function setImageScale(nextScale, clientX = null, clientY = null) {
  const view = state.imageView;
  if (!view.displayWidth || !view.displayHeight) return;
  const oldScale = view.scale;
  const scale = Math.max(view.minScale, Math.min(view.maxScale, nextScale));
  if (Math.abs(scale - oldScale) < 0.001) return;
  const stageBounds = imageStage.getBoundingClientRect();
  const surfaceBounds = imageSurface.getBoundingClientRect();
  const pointerInsideImage = clientX != null
    && clientY != null
    && clientX >= surfaceBounds.left
    && clientX <= surfaceBounds.right
    && clientY >= surfaceBounds.top
    && clientY <= surfaceBounds.bottom;
  const anchorX = pointerInsideImage
    ? clientX - stageBounds.left
    : view.translateX + (view.displayWidth * oldScale) / 2;
  const anchorY = pointerInsideImage
    ? clientY - stageBounds.top
    : view.translateY + (view.displayHeight * oldScale) / 2;
  const contentX = (anchorX - view.translateX) / oldScale;
  const contentY = (anchorY - view.translateY) / oldScale;
  view.scale = scale;
  view.translateX = anchorX - contentX * scale;
  view.translateY = anchorY - contentY * scale;
  applyImageTransform();
}

function beginImagePan(event) {
  const view = state.imageView;
  view.panning = true;
  view.panStartX = event.clientX;
  view.panStartY = event.clientY;
  view.panOriginX = view.translateX;
  view.panOriginY = view.translateY;
  applyImageTransform();
  canvas.setPointerCapture(event.pointerId);
  event.preventDefault();
}

function endImagePan() {
  state.imageView.panning = false;
  applyImageTransform();
}

function clearCanvas() {
  context.clearRect(0, 0, canvas.width, canvas.height);
}

function roiRect(roi) {
  return {
    x: roi.x_ratio * canvas.width,
    y: roi.y_ratio * canvas.height,
    width: roi.width_ratio * canvas.width,
    height: roi.height_ratio * canvas.height,
  };
}

function clampRect(rect) {
  const minimum = 16;
  const width = Math.max(minimum, Math.min(rect.width, canvas.width));
  const height = Math.max(minimum, Math.min(rect.height, canvas.height));
  return {
    x: Math.max(0, Math.min(rect.x, canvas.width - width)),
    y: Math.max(0, Math.min(rect.y, canvas.height - height)),
    width,
    height,
  };
}

function drawHandles(rect) {
  const size = 10;
  const points = [
    [rect.x, rect.y],
    [rect.x + rect.width, rect.y],
    [rect.x, rect.y + rect.height],
    [rect.x + rect.width, rect.y + rect.height],
  ];
  context.fillStyle = "#fff";
  context.strokeStyle = "#ff6b35";
  context.lineWidth = 2;
  points.forEach(([x, y]) => {
    context.fillRect(x - size / 2, y - size / 2, size, size);
    context.strokeRect(x - size / 2, y - size / 2, size, size);
  });
}

function drawCanvas() {
  clearCanvas();
  if (!state.recipe) return;
  state.harnessSegments.forEach((segment) => {
    const polygon = segment.polygon || [];
    if (polygon.length < 3) return;
    context.beginPath();
    polygon.forEach(([xRatio, yRatio], pointIndex) => {
      const x = xRatio * canvas.width;
      const y = yRatio * canvas.height;
      if (pointIndex === 0) context.moveTo(x, y);
      else context.lineTo(x, y);
    });
    context.closePath();
    const sam2Segment = segment.engine === "SAM2.1_HIERA_SMALL";
    context.fillStyle = sam2Segment ? "rgba(6,182,212,.22)" : "rgba(249,115,22,.25)";
    context.strokeStyle = sam2Segment ? "#0891b2" : "#f97316";
    context.lineWidth = 3;
    context.fill();
    context.stroke();
  });
  state.discoveryCandidates.forEach((candidate) => {
    const selected = candidate.candidate_id === state.selectedCandidateId;
    const segmented = candidate.target_kind === "HARNESS_SEGMENT";
    const rect = selected && state.workingRect ? state.workingRect : candidateRect(candidate);
    const { x, y, width, height } = rect;
    context.fillStyle = segmented
      ? (selected ? "rgba(6,182,212,.16)" : "rgba(6,182,212,.06)")
      : (selected ? "rgba(124,58,237,.14)" : "rgba(124,58,237,.07)");
    context.strokeStyle = segmented
      ? (selected ? "#0e7490" : "#0891b2")
      : (selected ? "#6d28d9" : "#8b5cf6");
    context.lineWidth = selected ? 4 : 2;
    context.setLineDash([8, 5]);
    context.fillRect(x, y, width, height);
    context.strokeRect(x, y, width, height);
    context.setLineDash([]);
    context.fillStyle = segmented ? "#0e7490" : "#5b21b6";
    context.font = "700 12px Segoe UI, sans-serif";
    context.fillText(`${segmented ? "分割" : "AI"} · ${candidate.label}`, x + 7, y + 17);
    if (selected) drawHandles(rect);
  });
  state.recipe.rois.forEach((roi) => {
    const selected = roi.id === state.selectedRoiId;
    const rect = selected && state.workingRect ? state.workingRect : roiRect(roi);
    const { x, y, width, height } = rect;
    context.fillStyle = selected ? "rgba(255,107,53,.15)" : "rgba(47,117,223,.10)";
    context.strokeStyle = selected ? "#ff6b35" : "#2f75df";
    context.lineWidth = selected ? 4 : 2;
    context.fillRect(x, y, width, height);
    context.strokeRect(x, y, width, height);
    context.fillStyle = selected ? "#e64f18" : "#245db7";
    context.font = "700 13px Segoe UI, sans-serif";
    context.fillText(roi.code, x + 7, y + 18);
    if (selected) drawHandles(rect);
  });
  if (state.pendingRect) {
    context.strokeStyle = "#ff6b35";
    context.fillStyle = "rgba(255,107,53,.12)";
    context.lineWidth = 3;
    context.setLineDash([8, 5]);
    context.fillRect(state.pendingRect.x, state.pendingRect.y, state.pendingRect.width, state.pendingRect.height);
    context.strokeRect(state.pendingRect.x, state.pendingRect.y, state.pendingRect.width, state.pendingRect.height);
    context.setLineDash([]);
  }
}

function pointerPosition(event) {
  const stageBounds = imageStage.getBoundingClientRect();
  const view = state.imageView;
  return {
    x: (event.clientX - stageBounds.left - view.translateX) / view.scale,
    y: (event.clientY - stageBounds.top - view.translateY) / view.scale,
  };
}

function hitRoi(point) {
  return [...(state.recipe?.rois || [])].reverse().find((roi) => {
    const { x, y, width, height } = roiRect(roi);
    return point.x >= x && point.x <= x + width && point.y >= y && point.y <= y + height;
  });
}

function hitCandidate(point) {
  return [...state.discoveryCandidates].reverse().find((candidate) => {
    const { x, y, width, height } = candidateRect(candidate);
    return point.x >= x && point.x <= x + width && point.y >= y && point.y <= y + height;
  });
}

function hitResizeHandle(point) {
  const candidate = selectedCandidate();
  const roi = selectedRoi();
  if (!candidate && !roi) return null;
  const rect = state.workingRect || (candidate ? candidateRect(candidate) : roiRect(roi));
  const handles = {
    nw: [rect.x, rect.y],
    ne: [rect.x + rect.width, rect.y],
    sw: [rect.x, rect.y + rect.height],
    se: [rect.x + rect.width, rect.y + rect.height],
  };
  return Object.entries(handles).find(([, [x, y]]) =>
    Math.abs(point.x - x) <= 12 && Math.abs(point.y - y) <= 12)?.[0] || null;
}

canvas.addEventListener("pointerdown", (event) => {
  if (!state.recipe?.base_image_url || state.savingRoi) return;
  if (state.imageView.panMode || state.imageView.spacePressed || event.button === 1) {
    beginImagePan(event);
    return;
  }
  const point = pointerPosition(event);
  const handle = hitResizeHandle(point);
  const existing = hitRoi(point);
  const candidate = existing ? null : hitCandidate(point);
  state.drawing = true;
  state.startPoint = point;
  state.pointerCandidateRoi = existing || candidate;
  state.pendingRect = null;
  state.workingRect = null;
  if (handle && selectedCandidate()) {
    state.interactionMode = `candidate-resize-${handle}`;
    state.originalRect = candidateRect(selectedCandidate());
  } else if (handle && selectedRoi()) {
    state.interactionMode = `resize-${handle}`;
    state.originalRect = roiRect(selectedRoi());
  } else if (existing) {
    state.selectedCandidateId = null;
    if (existing.id !== state.selectedRoiId) selectRoi(existing.id);
    state.interactionMode = "potential-move";
    state.originalRect = roiRect(existing);
  } else if (candidate) {
    if (candidate.candidate_id !== state.selectedCandidateId) selectCandidate(candidate.candidate_id);
    state.interactionMode = "candidate-potential-move";
    state.originalRect = candidateRect(candidate);
  } else {
    state.interactionMode = "draw";
    state.selectedRoiId = null;
    state.selectedCandidateId = null;
  }
  canvas.setPointerCapture(event.pointerId);
});

canvas.addEventListener("pointermove", (event) => {
  if (state.imageView.panning) {
    state.imageView.translateX = state.imageView.panOriginX + event.clientX - state.imageView.panStartX;
    state.imageView.translateY = state.imageView.panOriginY + event.clientY - state.imageView.panStartY;
    applyImageTransform();
    return;
  }
  if (!state.drawing) return;
  const point = pointerPosition(event);
  const dx = point.x - state.startPoint.x;
  const dy = point.y - state.startPoint.y;
  if (state.interactionMode === "potential-move" && (Math.abs(dx) > 3 || Math.abs(dy) > 3)) {
    state.interactionMode = "move";
  }
  if (state.interactionMode === "candidate-potential-move" && (Math.abs(dx) > 3 || Math.abs(dy) > 3)) {
    state.interactionMode = "candidate-move";
  }
  if (state.interactionMode === "draw") {
    state.pendingRect = clampRect({
      x: Math.min(state.startPoint.x, point.x),
      y: Math.min(state.startPoint.y, point.y),
      width: Math.abs(dx),
      height: Math.abs(dy),
    });
  } else if (["move", "candidate-move"].includes(state.interactionMode)) {
    state.workingRect = clampRect({
      ...state.originalRect,
      x: state.originalRect.x + dx,
      y: state.originalRect.y + dy,
    });
  } else if (state.interactionMode?.includes("resize-")) {
    const handle = state.interactionMode.replace("candidate-resize-", "").replace("resize-", "");
    const original = state.originalRect;
    const next = { ...original };
    if (handle.includes("n")) {
      next.y = original.y + dy;
      next.height = original.height - dy;
    }
    if (handle.includes("s")) next.height = original.height + dy;
    if (handle.includes("w")) {
      next.x = original.x + dx;
      next.width = original.width - dx;
    }
    if (handle.includes("e")) next.width = original.width + dx;
    state.workingRect = clampRect(next);
  }
  drawCanvas();
});

canvas.addEventListener("pointerup", async () => {
  if (state.imageView.panning) {
    endImagePan();
    return;
  }
  if (!state.drawing) return;
  state.drawing = false;
  const mode = state.interactionMode;
  const candidate = state.pointerCandidateRoi;
  state.pointerCandidateRoi = null;
  state.interactionMode = null;
  if (mode === "draw" && state.pendingRect?.width > 8 && state.pendingRect?.height > 8) {
    await createRoiFromRect(state.pendingRect);
    return;
  }
  if ((mode === "move" || mode?.startsWith("resize-")) && state.workingRect && selectedRoi()) {
    await persistRoiRect(selectedRoi(), state.workingRect);
    return;
  }
  if ((mode === "candidate-move" || mode?.startsWith("candidate-resize-")) && state.workingRect && selectedCandidate()) {
    persistCandidateRect(selectedCandidate(), state.workingRect);
    return;
  }
  state.pendingRect = null;
  state.workingRect = null;
  if (candidate?.candidate_id) {
    selectCandidate(candidate.candidate_id);
  } else if (candidate) {
    selectRoi(candidate.id);
  } else {
    drawCanvas();
  }
});

canvas.addEventListener("pointercancel", () => {
  if (state.imageView.panning) endImagePan();
});

baseImage.addEventListener("load", () => {
  syncCanvas(state.imageView.resetOnLoad);
  state.imageView.resetOnLoad = false;
});
window.addEventListener("resize", () => {
  if (baseImage.src) syncCanvas(true);
});

function selectedRoi() {
  return state.recipe?.rois.find((roi) => roi.id === state.selectedRoiId) || null;
}

function selectRoi(roiId) {
  state.selectedRoiId = roiId;
  state.selectedCandidateId = null;
  state.pendingRect = null;
  state.workingRect = null;
  renderConfiguredObjects();
  drawCanvas();
}

function persistCandidateRect(candidate, rect) {
  candidate.x_ratio = rect.x / canvas.width;
  candidate.y_ratio = rect.y / canvas.height;
  candidate.width_ratio = rect.width / canvas.width;
  candidate.height_ratio = rect.height / canvas.height;
  candidate.bbox = [
    candidate.x_ratio,
    candidate.y_ratio,
    candidate.x_ratio + candidate.width_ratio,
    candidate.y_ratio + candidate.height_ratio,
  ];
  state.workingRect = null;
  renderDiscoveryCandidates();
  drawCanvas();
  notify("AI 候选框位置和大小已调整，确认后才会写入正式配方。", "info", false);
}

async function createRoiFromRect(rect) {
  state.savingRoi = true;
  const nextIndex = state.recipe.rois.length + 1;
  try {
    const created = await request(`${api}/configuration/recipes/${state.recipe.id}/rois`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        code: `ROI_${nextIndex}`,
        name: `检测区域 ${nextIndex}`,
        object_type: "OBJECT",
        padding: 0,
        sort_order: nextIndex - 1,
        x_ratio: rect.x / canvas.width,
        y_ratio: rect.y / canvas.height,
        width_ratio: rect.width / canvas.width,
        height_ratio: rect.height / canvas.height,
      }),
    });
    let referenceError = null;
    try {
      await request(`${api}/configuration/rois/${created.id}/capture-reference`, {
        method: "POST",
      });
    } catch (error) {
      referenceError = error;
    }
    state.pendingRect = null;
    await loadRecipe(state.recipe.id);
    await waitForBaseImage();
    selectRoi(created.id);
    openObjectModal(created.id);
    if (referenceError) {
      notify(`检测区域已保存，但标准参考图生成失败：${referenceError.message}`, "warning", false);
    } else {
      notify("检测区域和标准参考图已自动保存，可继续配置颜色或文字规则", "info", false);
    }
  } catch (error) {
    state.pendingRect = null;
    drawCanvas();
    notify(error.message, "danger", false);
  } finally {
    state.savingRoi = false;
  }
}

function waitForBaseImage() {
  if (baseImage.complete && baseImage.naturalWidth) return Promise.resolve();
  return new Promise((resolve) => {
    const timer = window.setTimeout(resolve, 1800);
    baseImage.addEventListener("load", () => {
      window.clearTimeout(timer);
      resolve();
    }, { once: true });
  });
}

async function persistRoiRect(roi, rect) {
  state.savingRoi = true;
  try {
    await request(`${api}/configuration/rois/${roi.id}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        code: roi.code,
        name: roi.name,
        object_type: roi.object_type,
        padding: roi.padding || 0,
        sort_order: roi.sort_order || 0,
        x_ratio: rect.x / canvas.width,
        y_ratio: rect.y / canvas.height,
        width_ratio: rect.width / canvas.width,
        height_ratio: rect.height / canvas.height,
      }),
    });
    let referenceWarning = null;
    try {
      const reference = await request(`${api}/configuration/rois/${roi.id}/capture-reference`, {
        method: "POST",
      });
      referenceWarning = reference.embedding_warning || null;
    } catch (error) {
      referenceWarning = error.message;
    }
    const roiId = roi.id;
    state.workingRect = null;
    await loadRecipe(state.recipe.id);
    selectRoi(roiId);
    if (referenceWarning) {
      notify(`检测区域已保存，标准参考图已更新；特征向量待重试：${referenceWarning}`, "warning", false);
    } else {
      notify("检测区域、标准参考图和特征向量已同步更新", "success", false);
    }
  } catch (error) {
    state.workingRect = null;
    drawCanvas();
    notify(error.message, "danger", false);
  } finally {
    state.savingRoi = false;
  }
}

const capabilityMeta = {
  EXISTENCE: ["存在校验", "✓"],
  REFERENCE_SIMILARITY: ["型号相似度", "◫"],
  COLOR_RATIO: ["颜色校验", "●"],
  OCR_TEXT: ["OCR 文字", "Aa"],
  VLM_JUDGEMENT: ["复杂装配判断", "AI"],
};

const sceneMeta = {
  OBJECT_EXISTENCE: { model: "DINOv2", ruleType: "EXISTENCE", label: "物体存在" },
  COLOR_ATTRIBUTE: { model: "OpenCV", ruleType: "COLOR", label: "颜色" },
  TEXT_OCR: { model: "PaddleOCR", ruleType: "TEXT", label: "OCR 文字识别" },
};

function inferItemScene(item, roi) {
  const configured = item?.rule_json?.scene_type;
  if (configured && sceneMeta[configured]) return configured;
  if (item?.capability === "OCR_TEXT") return "TEXT_OCR";
  if (item?.capability === "COLOR_RATIO") return "COLOR_ATTRIBUTE";
  return "OBJECT_EXISTENCE";
}

function itemCapability(item) {
  return ["PRESENCE", "EXISTENCE"].includes(item.inspection_type)
    ? "EXISTENCE"
    : item.capability;
}

function describeRule(item) {
  const capability = itemCapability(item);
  if (capability === "EXISTENCE") {
    return String(item.rule_json.min_similarity ?? 0.9);
  }
  if (capability === "REFERENCE_SIMILARITY") {
    return String(item.expected_json.class_code || item.rule_json.min_similarity || "-");
  }
  if (capability === "COLOR_RATIO") {
    return String(item.expected_json.color || "-").toLowerCase();
  }
  return String(item.expected_json.text || "-");
}

function renderConfiguredObjects() {
  const rois = state.recipe?.rois || [];
  byId("configuredObjectList").innerHTML = rois.length
    ? rois.map((roi, index) => `
        <article class="configured-object-card ${roi.id === state.selectedRoiId ? "selected" : ""}" data-detail-roi="${roi.id}">
          <div class="configured-object-heading">
            <span>${String(index + 1).padStart(2, "0")}</span>
            <div class="configured-object-identity">
              <small>${escapeHtml(roi.object_type || "OBJECT")}</small>
              <strong>${escapeHtml(roi.code)}</strong>
            </div>
            <div class="configured-object-quick-actions">
              <button class="btn btn-sm btn-outline-primary edit-object" type="button">编辑</button>
              <button class="btn btn-sm btn-outline-danger delete-object" type="button">删除</button>
            </div>
          </div>
          <div class="configured-object-summary">
            <span>${roi.inspection_items.length} 条校验规则</span>
            ${roi.inspection_items.some((item) => item.rule_json.vlm_review_enabled)
              ? '<span class="review-enabled">VLM 复核</span>'
              : "<span>仅主模型</span>"}
          </div>
          <details class="configured-object-rule-details">
            <summary>查看规则明细</summary>
            <div class="configured-object-rules">
              ${roi.inspection_items.length
                ? roi.inspection_items.map((item) => `
                    <span>${escapeHtml(capabilityMeta[itemCapability(item)]?.[0] || item.capability)} · ${escapeHtml(describeRule(item))}</span>`).join("")
                : "<em>尚未配置校验规则</em>"}
            </div>
          </details>
        </article>`).join("")
    : `
      <div class="no-object-selected compact">
        <span>⌖</span>
        <strong>还没有检测区域</strong>
        <p>直接在左侧图片空白位置拖动画框，区域会自动保存并弹出规则配置。</p>
      </div>`;
}

function openObjectModal(roiId) {
  selectRoi(roiId);
  populateObjectEditor();
  const modal = byId("objectConfigModal");
  modal.classList.add("show");
  modal.style.display = "block";
  modal.setAttribute("aria-modal", "true");
  modal.removeAttribute("aria-hidden");
  document.body.classList.add("modal-open");
  if (!document.querySelector(".custom-modal-backdrop")) {
    const backdrop = document.createElement("div");
    backdrop.className = "modal-backdrop fade show custom-modal-backdrop";
    document.body.appendChild(backdrop);
  }
}

function closeObjectModal() {
  const modal = byId("objectConfigModal");
  modal.classList.remove("show");
  modal.style.display = "none";
  modal.removeAttribute("aria-modal");
  modal.setAttribute("aria-hidden", "true");
  document.body.classList.remove("modal-open");
  document.querySelector(".custom-modal-backdrop")?.remove();
}

function populateObjectEditor() {
  const roi = selectedRoi();
  if (!roi) return;
  resetInlineRoiTest();
  byId("roiRuleStatus").textContent = "";
  byId("roiRuleStatus").className = "roi-rule-status";
  byId("selectedObjectTitle").textContent = roi.code;
  fillObjectTypeSelect(roi.object_type || "OBJECT");
  byId("roiPointX").value = `${Math.round(roi.x_ratio * baseImage.naturalWidth)} px`;
  byId("roiPointY").value = `${Math.round(roi.y_ratio * baseImage.naturalHeight)} px`;
  byId("roiPointWidth").value = `${Math.round(roi.width_ratio * baseImage.naturalWidth)} px`;
  byId("roiPointHeight").value = `${Math.round(roi.height_ratio * baseImage.naturalHeight)} px`;
  updateRoiReferencePreview(roi);
  const configuredRules = roi.inspection_items.map((item) => {
    const capability = itemCapability(item);
    if (capability === "PRESENCE" || capability === "EXISTENCE" || item.capability === "REFERENCE_SIMILARITY") {
      return {
        type: "EXISTENCE",
        value: String(item.rule_json.min_similarity ?? 0.9),
        scene: inferItemScene(item, roi),
      };
    }
    if (capability === "COLOR_RATIO") {
      return { type: "COLOR", scene: inferItemScene(item, roi), value: String(item.expected_json.color || "").toLowerCase() };
    }
    if (capability === "VLM_JUDGEMENT") return null;
    return { type: "TEXT", scene: inferItemScene(item, roi), value: String(item.expected_json.text || "") };
  }).filter(Boolean);
  const existenceItem = roi.inspection_items.find(
    (item) => itemCapability(item) === "EXISTENCE" || item.capability === "REFERENCE_SIMILARITY",
  );
  const reviewItem = roi.inspection_items.find((item) => item.rule_json.vlm_review_enabled)
    || existenceItem;
  const reviewRule = reviewItem?.rule_json || {};
  const minimumSimilarity = Number(reviewRule.min_similarity ?? 0.9);
  byId("vlmReviewEnabled").checked = Boolean(reviewRule.vlm_review_enabled);
  byId("vlmReviewMode").value = String(reviewRule.vlm_review_mode || "ALWAYS");
  byId("vlmReviewLower").value = String(
    reviewRule.vlm_review_lower ?? Math.max(0, minimumSimilarity - 0.05).toFixed(2),
  );
  byId("vlmReviewUpper").value = String(
    reviewRule.vlm_review_upper ?? Math.min(1, minimumSimilarity + 0.03).toFixed(2),
  );
  state.vlmPromptDirty = reviewRule.vlm_prompt_auto === false;
  byId("vlmReviewPrompt").value = String(reviewRule.vlm_prompt || "");
  state.draftRules = configuredRules.length
    ? configuredRules
    : [{
        type: "EXISTENCE",
        scene: "OBJECT_EXISTENCE",
        value: "0.9",
      }];
  renderRuleRows();
  refreshVlmPrompt(!reviewRule.vlm_prompt || !state.vlmPromptDirty);
  syncVlmReviewMode();
}

function updateRoiReferencePreview(roi) {
  const preview = byId("roiReferenceImage");
  const empty = byId("roiReferenceEmpty");
  if (roi.reference?.image_url) {
    preview.src = `${roi.reference.image_url}?v=${Date.now()}`;
    preview.style.display = "block";
    empty.style.display = "none";
    return;
  }
  if (!baseImage.complete || !baseImage.naturalWidth) {
    preview.removeAttribute("src");
    preview.style.display = "none";
    empty.style.display = "grid";
    return;
  }
  const cropCanvas = document.createElement("canvas");
  const sourceX = Math.round(roi.x_ratio * baseImage.naturalWidth);
  const sourceY = Math.round(roi.y_ratio * baseImage.naturalHeight);
  const sourceWidth = Math.max(1, Math.round(roi.width_ratio * baseImage.naturalWidth));
  const sourceHeight = Math.max(1, Math.round(roi.height_ratio * baseImage.naturalHeight));
  cropCanvas.width = sourceWidth;
  cropCanvas.height = sourceHeight;
  cropCanvas.getContext("2d").drawImage(
    baseImage,
    sourceX,
    sourceY,
    sourceWidth,
    sourceHeight,
    0,
    0,
    sourceWidth,
    sourceHeight,
  );
  preview.src = cropCanvas.toDataURL("image/jpeg", 0.88);
  preview.style.display = "block";
  empty.style.display = "none";
}

const validationTypeLabels = {
  EXISTENCE: "存在校验",
  COLOR: "颜色校验",
  TEXT: "文本校验",
};

function ruleValuePlaceholder(type) {
  if (type === "EXISTENCE") return "0.9";
  if (type === "COLOR") return "自动识别，可手动修改";
  return "输入需要校验的文字";
}

const colorDisplayNames = {
  yellow: "黄色",
  red: "红色",
  blue: "蓝色",
  green: "绿色",
  white: "白色",
  black: "黑色",
  orange: "橙色",
  gray: "灰色",
};

function colorRuleValueEditor(rule, index) {
  const color = String(rule.value || "").toLowerCase();
  const colorName = colorDisplayNames[color] || color || "待识别";
  const detail = rule.colorAnalysis
    ? `${rule.colorAnalysis.display_name} · ${(Number(rule.colorAnalysis.ratio) * 100).toFixed(1)}%`
    : colorName;
  return `
    <div class="color-rule-editor">
      <span class="color-rule-swatch" style="background:${escapeHtml(rule.colorAnalysis?.hex || color || "#e2e8f0")}"></span>
      <input class="form-control rule-row-value" value="${escapeHtml(rule.value)}" placeholder="${ruleValuePlaceholder(rule.type)}">
      <button class="btn btn-sm btn-outline-primary detect-rule-color" type="button" data-color-rule-index="${index}">自动识别</button>
      <small>${escapeHtml(detail)}</small>
    </div>`;
}

const objectTypeLabels = {
  FUSE: "保险丝",
  SCREW: "螺丝",
  CONNECTOR: "连接器",
  HARNESS: "线束",
  PCBA: "PCBA",
  BUSBAR: "铜排",
  LABEL: "标签",
  OBJECT: "目标物体",
};

function rulePromptDescription(rule) {
  const scene = sceneMeta[rule.scene]?.label || "检测场景";
  if (rule.type === "EXISTENCE") return `${scene}：目标必须存在且与标准参考图一致，相似度阈值为${rule.value || "未填写"}`;
  if (rule.type === "COLOR") return `${scene}：目标颜色应为${rule.value || "未填写"}`;
  if (rule.type === "TEXT") return `${scene}：识别文字必须包含“${rule.value || "未填写"}”`;
  return `${scene}：${rule.value || "未填写"}`;
}

function generatedVlmPrompt() {
  const objectType = byId("roiObjectType").value || "OBJECT";
  const configuredType = state.referenceObjectTypes.find((item) => item.code === objectType);
  const objectName = configuredType?.name || objectTypeLabels[objectType] || objectType;
  const requirements = state.draftRules.map((rule, index) => `${index + 1}. ${rulePromptDescription(rule)}`).join("\n");
  return `只检查图片中的${objectName}检测区域，不要分析区域外内容。\n请复核以下规则：\n${requirements || "1. 检查目标状态是否符合要求"}\n不得根据常识猜测；看不清时返回UNCERTAIN。只返回结构化JSON，包含result、confidence和reason。`;
}

function refreshVlmPrompt(force = false) {
  if (state.vlmPromptDirty && !force) return;
  byId("vlmReviewPrompt").value = generatedVlmPrompt();
  state.vlmPromptDirty = false;
}

function syncVlmReviewMode() {
  const enabled = byId("vlmReviewEnabled").checked;
  const lowConfidence = byId("vlmReviewMode").value === "LOW_CONFIDENCE";
  byId("vlmReviewMode").disabled = !enabled;
  byId("vlmReviewLower").disabled = !enabled || !lowConfidence;
  byId("vlmReviewUpper").disabled = !enabled || !lowConfidence;
  byId("vlmReviewLowerField").hidden = !enabled || !lowConfidence;
  byId("vlmReviewUpperField").hidden = !enabled || !lowConfidence;
  byId("vlmReviewPrompt").disabled = !enabled;
  byId("regenerateVlmPrompt").disabled = !enabled;
  byId("vlmReviewModeNote").textContent = !enabled
    ? "未启用 VLM 复核，生产检测和当前 ROI 测试均只显示主模型结果。"
    : lowConfidence
      ? "生产检测仅在主模型分数处于下限与上限之间时复核；低于下限直接 NG，高于上限直接采用主模型。测试当前 ROI 时仍强制执行复核，便于对比。"
      : "生产检测每次都执行 VLM 复核，不参考上下限。测试当前 ROI 时同样执行复核。";
}

function renderRuleRows() {
  byId("ruleRows").innerHTML = state.draftRules.length
    ? state.draftRules.map((rule, index) => `
        <div class="rule-table-row" data-rule-index="${index}">
          <span class="rule-row-index">${index + 1}</span>
          <select class="form-select rule-row-scene">
            ${Object.entries(sceneMeta).map(([value, meta]) => `<option value="${value}" ${rule.scene === value ? "selected" : ""}>${escapeHtml(meta.label)}</option>`).join("")}
          </select>
          <span class="rule-capability-label">${escapeHtml(validationTypeLabels[rule.type])}</span>
          ${rule.type === "COLOR"
            ? colorRuleValueEditor(rule, index)
            : `<input class="form-control rule-row-value" value="${escapeHtml(rule.value)}" placeholder="${ruleValuePlaceholder(rule.type)}" ${rule.type === "EXISTENCE" ? 'type="number" min="0" max="1" step="0.01"' : ""}>`}
          <button class="btn btn-sm btn-outline-danger remove-rule-row" type="button">删除</button>
        </div>`).join("")
    : `
      <div class="empty-rule-state table-empty">
        <span>＋</span><strong>尚未配置校验规则</strong>
        <p>点击“添加规则”新增一行校验项目。</p>
      </div>`;
}

function addRuleRow() {
  const index = state.draftRules.length;
  state.draftRules.push({ type: "COLOR", scene: "COLOR_ATTRIBUTE", value: "", colorAnalysis: null });
  renderRuleRows();
  refreshVlmPrompt();
  detectColorForRule(index);
}

async function detectColorForRule(index) {
  const roi = selectedRoi();
  const rule = state.draftRules[index];
  if (!roi || !rule || rule.type !== "COLOR") return;
  const button = byId("ruleRows").querySelector(`[data-color-rule-index="${index}"]`);
  if (button) {
    button.disabled = true;
    button.textContent = "识别中...";
  }
  try {
    const result = await request(`${api}/configuration/rois/${roi.id}/analyze-color`, {
      method: "POST",
    });
    rule.value = String(result.color || "").toLowerCase();
    rule.colorAnalysis = result;
    renderRuleRows();
    refreshVlmPrompt();
    notify(`已识别为${result.display_name}，颜色占比 ${(Number(result.ratio) * 100).toFixed(1)}%，可手动修改`, "success", false);
  } catch (error) {
    notify(`颜色自动识别失败：${error.message}`, "warning", false);
    renderRuleRows();
  }
}

async function saveRoiRules() {
  const roi = selectedRoi();
  if (!roi) return false;
  if (!state.draftRules.length) {
    notify("请至少配置一条检测场景规则", "warning", false);
    return false;
  }
  for (const rule of state.draftRules) {
    const value = rule.value.trim();
    if (!value) {
      notify("每条规则都必须填写校验值", "warning", false);
      return false;
    }
    if (rule.type === "EXISTENCE" && (!Number.isFinite(Number(value)) || Number(value) <= 0 || Number(value) > 1)) {
      notify("存在校验的相似度阈值必须大于 0 且不超过 1", "warning", false);
      return false;
    }
    if (rule.type === "COLOR" && !/^[a-zA-Z]+$/.test(value)) {
      notify("颜色校验请填写英文颜色名称，例如 yellow", "warning", false);
      return false;
    }
  }
  const objectName = roi.code;
  const objectType = byId("roiObjectType").value;
  const reviewEnabled = byId("vlmReviewEnabled").checked;
  const reviewMode = byId("vlmReviewMode").value;
  const reviewLower = Number(byId("vlmReviewLower").value);
  const reviewUpper = Number(byId("vlmReviewUpper").value);
  if (reviewEnabled && !byId("vlmReviewPrompt").value.trim()) refreshVlmPrompt(true);
  const reviewPrompt = byId("vlmReviewPrompt").value.trim();
  if (
    reviewEnabled
    && reviewMode === "LOW_CONFIDENCE"
    && (!Number.isFinite(reviewLower)
      || !Number.isFinite(reviewUpper)
      || reviewLower < 0
      || reviewUpper > 1
      || reviewLower > reviewUpper)
  ) {
    notify("VLM 复核区间必须位于 0 到 1，且下限不能大于上限", "warning", false);
    return false;
  }

  const saveButton = byId("saveRoiRules");
  const testButton = byId("testRoiRules");
  const status = byId("roiRuleStatus");
  saveButton.disabled = true;
  testButton.disabled = true;
  saveButton.textContent = "正在保存…";
  status.textContent = "正在保存规则并生成标准参考图…";
  status.className = "roi-rule-status working";
  try {
    const rect = roiRect(roi);
    await request(`${api}/configuration/rois/${roi.id}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        code: roi.code,
        name: objectName,
        object_type: objectType,
        padding: roi.padding || 0,
        sort_order: roi.sort_order || 0,
        x_ratio: rect.x / canvas.width,
        y_ratio: rect.y / canvas.height,
        width_ratio: rect.width / canvas.width,
        height_ratio: rect.height / canvas.height,
      }),
    });
    let reference = roi.reference || null;
    let embeddingWarning = null;
    if (state.draftRules.some((rule) => rule.type === "EXISTENCE")) {
      reference = await request(`${api}/configuration/rois/${roi.id}/capture-reference`, {
        method: "POST",
      });
      embeddingWarning = reference.embedding_warning || null;
    }
    for (const item of roi.inspection_items) {
      await request(`${api}/configuration/inspection-items/${item.id}`, { method: "DELETE" });
    }
    for (const [index, rule] of state.draftRules.entries()) {
      const value = rule.value.trim();
      const sceneType = rule.scene || "OBJECT_EXISTENCE";
      let payload;
      if (rule.type === "EXISTENCE") {
        payload = {
          inspection_type: "EXISTENCE",
          capability: "REFERENCE_SIMILARITY",
          reference_group_id: reference.group_id,
          expected_json: {
            exists: true,
            class_code: reference.class_code,
            reference_image_url: reference.image_url,
          },
          rule_json: {
            min_similarity: Number(value),
            scene_type: sceneType,
            primary_model: "DINOv2",
            vlm_review_enabled: reviewEnabled,
            vlm_review_mode: reviewMode,
            vlm_review_lower: reviewLower,
            vlm_review_upper: reviewUpper,
            vlm_prompt: reviewPrompt,
            vlm_prompt_auto: !state.vlmPromptDirty,
            vlm_uncertain_result: "NG",
          },
        };
      } else if (rule.type === "COLOR") {
        payload = {
          inspection_type: "COLOR",
          capability: "COLOR_RATIO",
          reference_group_id: null,
          expected_json: { color: value.toUpperCase() },
          rule_json: {
            min_ratio: 0.15,
            max_ratio: 1,
            scene_type: sceneType,
            primary_model: "OpenCV",
            vlm_review_enabled: reviewEnabled,
            vlm_review_mode: reviewMode,
            vlm_review_lower: reviewLower,
            vlm_review_upper: reviewUpper,
            vlm_prompt: reviewPrompt,
            vlm_prompt_auto: !state.vlmPromptDirty,
            vlm_uncertain_result: "NG",
          },
        };
      } else if (rule.type === "TEXT") {
        payload = {
          inspection_type: "TEXT",
          capability: "OCR_TEXT",
          reference_group_id: null,
          expected_json: { text: value },
          rule_json: {
            operator: "CONTAINS",
            case_sensitive: false,
            scene_type: sceneType,
            primary_model: "PaddleOCR",
            vlm_review_enabled: reviewEnabled,
            vlm_review_mode: reviewMode,
            vlm_review_lower: reviewLower,
            vlm_review_upper: reviewUpper,
            vlm_prompt: reviewPrompt,
            vlm_prompt_auto: !state.vlmPromptDirty,
            vlm_uncertain_result: "NG",
          },
        };
      }
      await request(`${api}/configuration/rois/${roi.id}/inspection-items`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          code: `${roi.code}_${rule.type}_${index + 1}`,
          name: `${validationTypeLabels[rule.type]} ${index + 1}`,
          execution_order: index,
          required: true,
          ...payload,
        }),
      });
    }
    const scene = await request(`${api}/world/recipes/${state.recipe.id}/sync`, {
      method: "POST",
    });
    const worldObject = scene.objects.find((item) => item.roi_ids.includes(roi.id));
    if (worldObject) {
      await request(`${api}/world/objects/${worldObject.id}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name: objectName,
          object_type: objectType,
          parent_object_id: worldObject.parent_object_id,
          location_mode: "FIXED_ROI",
          expected_state: worldObject.expected_state,
          perception_config: {
            ...worldObject.perception_config,
            vlm_review_enabled: reviewEnabled,
            vlm_review_mode: reviewMode,
            vlm_review_lower: reviewLower,
            vlm_review_upper: reviewUpper,
          },
          sort_order: roi.sort_order || 0,
          enabled: true,
        }),
      });
    }
    const roiId = roi.id;
    await loadRecipe(state.recipe.id);
    selectRoi(roiId);
    populateObjectEditor();
    status.textContent = "保存成功";
    status.className = "roi-rule-status success";
    notify(
      embeddingWarning
        ? "规则已保存；DINOv2当前不可用，参考向量已标记为待生成"
        : "当前 ROI 的校验规则已保存",
      embeddingWarning ? "warning" : "success",
      false,
    );
    return true;
  } catch (error) {
    status.textContent = `保存失败：${error.message}`;
    status.className = "roi-rule-status error";
    notify(error.message, "danger", false);
    return false;
  } finally {
    saveButton.disabled = false;
    testButton.disabled = false;
    saveButton.textContent = "保存当前 ROI 规则";
  }
}

async function testCurrentRoiRules() {
  const roi = selectedRoi();
  const roiId = state.selectedRoiId;
  const recipeId = state.recipe?.id;
  if (!roi || !roiId || !recipeId) return;
  if (!state.draftRules.length) {
    notify("请至少配置一条校验规则后再测试", "warning", false);
    return;
  }
  for (const rule of state.draftRules) {
    const value = rule.value.trim();
    if (!value) {
      notify("每条规则都必须填写校验值", "warning", false);
      return;
    }
    if (rule.type === "EXISTENCE" && (!Number.isFinite(Number(value)) || Number(value) <= 0 || Number(value) > 1)) {
      notify("存在校验的相似度阈值必须大于 0 且不超过 1", "warning", false);
      return;
    }
  }
  const review = {
    enabled: byId("vlmReviewEnabled").checked,
    mode: byId("vlmReviewMode").value,
    lower: Number(byId("vlmReviewLower").value),
    upper: Number(byId("vlmReviewUpper").value),
    prompt: byId("vlmReviewPrompt").value.trim(),
    prompt_auto: !state.vlmPromptDirty,
  };
  const testButton = byId("testRoiRules");
  const status = byId("roiRuleStatus");
  testButton.disabled = true;
  testButton.textContent = "正在测试…";
  status.textContent = "正在执行当前 ROI 的临时规则，请稍候…";
  status.className = "roi-rule-status working";
  showInlineRoiTestLoading(roi, state.draftRules, review);
  try {
    let reference = roi.reference || null;
    if (state.draftRules.some((rule) => rule.type === "EXISTENCE") && !reference?.group_id) {
      reference = await request(`${api}/configuration/rois/${roi.id}/capture-reference`, {
        method: "POST",
      });
    }
    const rules = state.draftRules.map((rule) => ({
      type: rule.type,
      scene: rule.scene,
      value: rule.value.trim(),
      reference_group_id: rule.type === "EXISTENCE" ? reference?.group_id : null,
      class_code: rule.type === "EXISTENCE" ? reference?.class_code : null,
    }));
    const response = await fetch(state.recipe.base_image_url);
    if (!response.ok) throw new Error("无法读取当前配方图片");
    const blob = await response.blob();
    const file = new File([blob], `roi-${roiId}-test.jpg`, {
      type: blob.type || "image/jpeg",
    });
    const data = new FormData();
    data.append("recipe_id", recipeId);
    data.append("roi_id", roiId);
    data.append("draft_rules", JSON.stringify(rules));
    data.append("review_config", JSON.stringify(review));
    data.append("file", file);
    const result = await request(`${api}/inspection/test`, { method: "POST", body: data });
    renderInlineRoiTestResult(result, roi, state.draftRules, review);
    status.textContent = `测试完成：${result.result}，共 ${state.draftRules.length} 条规则`;
    status.className = `roi-rule-status ${result.result === "OK" ? "success" : "error"}`;
  } catch (error) {
    renderInlineRoiTestError(error, roi);
    status.textContent = `测试失败：${error.message}`;
    status.className = "roi-rule-status error";
  } finally {
    testButton.disabled = false;
    testButton.textContent = "测试当前 ROI";
  }
}

function resetInlineRoiTest() {
  const panel = byId("roiInlineTestPanel");
  if (!panel) return;
  panel.hidden = true;
  byId("roiInlineTestTitle").textContent = "等待测试";
  byId("roiInlineTestSummary").textContent = "测试不会保存规则，也不会离开当前配置页面。";
  byId("roiInlineTestBadge").className = "result-badge waiting";
  byId("roiInlineTestBadge").textContent = "WAITING";
  byId("roiInlineTestOverview").innerHTML = "";
  byId("roiInlineRuleResults").innerHTML = "";
  byId("roiInlineReferenceImage").removeAttribute("src");
  byId("roiInlineReferenceImage").hidden = true;
  byId("roiInlineReferenceEmpty").hidden = false;
  byId("roiInlineTestImage").removeAttribute("src");
}

function showInlineRoiTestLoading(roi, rules, review) {
  const panel = byId("roiInlineTestPanel");
  panel.hidden = false;
  byId("roiInlineTestTitle").textContent = `${roi.code} 正在测试`;
  byId("roiInlineTestSummary").textContent = `正在执行 ${rules.length} 条规则${review.enabled ? "，并同步执行 Qwen3-VL 复核" : ""}。`;
  byId("roiInlineTestBadge").className = "result-badge waiting";
  byId("roiInlineTestBadge").textContent = "RUNNING";
  byId("roiInlineTestOverview").innerHTML = `
    <div><small>当前区域</small><strong>${escapeHtml(roi.code)}</strong></div>
    <div><small>规则数量</small><strong>${rules.length}</strong></div>
    <div><small>VLM 复核</small><strong>${review.enabled ? "已启用" : "未启用"}</strong></div>`;
  byId("roiInlineRuleResults").innerHTML = '<div class="roi-inline-loading"><span></span>正在读取图片并执行检测…</div>';
  const referenceUrl = roi.reference?.image_url
    || byId("roiReferenceImage")?.getAttribute("src")
    || "";
  const referenceImage = byId("roiInlineReferenceImage");
  const referenceEmpty = byId("roiInlineReferenceEmpty");
  if (referenceUrl) {
    referenceImage.src = referenceUrl;
    referenceImage.hidden = false;
    referenceEmpty.hidden = true;
  } else {
    referenceImage.removeAttribute("src");
    referenceImage.hidden = true;
    referenceEmpty.hidden = false;
  }
  panel.scrollIntoView({ block: "nearest", behavior: "smooth" });
}

function ruleExpectedDisplay(rule) {
  if (rule.type === "EXISTENCE") return `相似度 ≥ ${Number(rule.value).toFixed(4)}`;
  if (rule.type === "COLOR") return `目标颜色 = ${rule.value}`;
  if (rule.type === "TEXT") return `识别文字包含“${rule.value}”`;
  return rule.value || "-";
}

function resultValueRows(values) {
  return Object.entries(values)
    .filter(([, value]) => value !== null && value !== undefined && value !== "")
    .map(([label, value]) => `<div><span>${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong></div>`)
    .join("");
}

function primaryResultValues(primary) {
  const details = primary.details || {};
  const values = {};
  if (details.similarity != null) values["相似度"] = Number(details.similarity).toFixed(4);
  if (details.matched_class) values["匹配类别"] = details.matched_class;
  if (details.matched_reference) values["命中参考图"] = String(details.matched_reference).split(/[\\/]/).pop();
  if (details.color) values["识别颜色"] = details.color;
  if (details.ratio != null) values["颜色占比"] = `${(Number(details.ratio) * 100).toFixed(2)}%`;
  if (details.text !== undefined) values["识别文字"] = details.text || "未识别到文字";
  if (String(primary.model || "").toUpperCase().includes("OCR")) values["OCR 执行方式"] = "专用 OCR 模型";
  if (details.confidence != null) values["OCR 置信度"] = Number(details.confidence).toFixed(4);
  if (!Object.keys(values).length && primary.score != null) values["模型分数"] = Number(primary.score).toFixed(4);
  return values;
}

function reviewResultValues(review) {
  const parsed = review?.parsed || {};
  const values = {};
  if (parsed.actual_text !== undefined) values["识别文字"] = parsed.actual_text || "未识别到文字";
  else if (parsed.text !== undefined) values["识别文字"] = parsed.text || "未识别到文字";
  else if (parsed.actual !== undefined) values["实际结果"] = typeof parsed.actual === "object" ? JSON.stringify(parsed.actual) : parsed.actual;
  if (parsed.color !== undefined) values["识别颜色"] = parsed.color;
  if (parsed.confidence != null) values["复核置信度"] = Number(parsed.confidence).toFixed(4);
  values["复核说明"] = parsed.reason || review?.error || (review?.parsed ? "模型未提供说明" : "模型返回内容无法解析，已按安全策略判定");
  return values;
}

function renderInlineRoiTestResult(result, roi, rules, reviewConfig) {
  const items = result.image_results?.[0]?.inspection_items || [];
  const passed = items.filter((item) => item.status === "OK").length;
  const badge = byId("roiInlineTestBadge");
  byId("roiInlineTestPanel").hidden = false;
  byId("roiInlineTestTitle").textContent = `${roi.code} 测试完成`;
  byId("roiInlineTestSummary").textContent = `${passed}/${items.length} 条规则通过；以下结果使用当前未保存的配置。`;
  badge.className = `result-badge ${String(result.result || "ERROR").toLowerCase()}`;
  badge.textContent = result.result || "ERROR";
  byId("roiInlineTestOverview").innerHTML = `
    <div><small>最终结论</small><strong>${result.result === "OK" ? "当前 ROI 通过" : "当前 ROI 未通过"}</strong></div>
    <div><small>规则通过</small><strong>${passed} / ${items.length}</strong></div>
    <div><small>总耗时</small><strong>${Number(result.elapsed_ms || 0).toFixed(2)} ms</strong></div>
    <div><small>VLM 复核</small><strong>${reviewConfig.enabled ? "已执行" : "未启用"}</strong></div>`;
  const roiImageUrl = result.image_results?.[0]?.roi_image_url;
  byId("roiInlineTestImage").src = `${roiImageUrl || `/results/${result.request_id}/${encodeURIComponent(roi.code)}.jpg`}?v=${Date.now()}`;
  byId("roiInlineRuleResults").innerHTML = items.map((item, index) => {
    const rule = rules[index] || {};
    const actual = item.actual || {};
    const primary = actual.primary_result || {
      model: item.primary_model || item.capability,
      status: actual.primary_status || item.status,
      score: item.score,
      details: actual,
      message: item.message,
    };
    const vlmReview = actual.vlm_review;
    const sceneLabel = sceneMeta[rule.scene]?.label || sceneMeta[item.scene_type]?.label || item.inspection_type;
    const primaryValues = resultValueRows(primaryResultValues(primary)) || '<div><span>模型输出</span><strong>无结构化输出</strong></div>';
    const reviewValues = vlmReview ? resultValueRows(reviewResultValues(vlmReview)) : "";
    return `
      <article class="roi-inline-rule-card ${String(item.status).toLowerCase()}">
        <header>
          <div><span>规则 ${index + 1} · ${escapeHtml(sceneLabel)}</span><strong>${escapeHtml(item.item_name)}</strong></div>
          <span class="result-badge ${String(item.status).toLowerCase()}">${escapeHtml(item.status)}</span>
        </header>
        <div class="roi-inline-rule-condition">
          <span>当前规则</span><strong>${escapeHtml(ruleExpectedDisplay(rule))}</strong>
        </div>
        <div class="roi-inline-model-grid ${reviewConfig.enabled ? "with-review" : ""}">
          <section class="roi-inline-model-card primary">
            <header><span>主检测模型</span><strong>${escapeHtml(primary.model || "未指定")}</strong><b class="${String(primary.status || "ERROR").toLowerCase()}">${escapeHtml(primary.status || "ERROR")}</b></header>
            <div class="roi-inline-value-grid">${primaryValues}</div>
            <p>${escapeHtml(primary.message || item.message || "无模型说明")}</p>
            <details><summary>查看主模型原始输出</summary><pre>${escapeHtml(JSON.stringify(primary.details || {}, null, 2))}</pre></details>
          </section>
          ${reviewConfig.enabled ? `<section class="roi-inline-model-card review ${vlmReview ? "executed" : "error"}">
            <header><span>VLM 复核</span><strong>${escapeHtml(vlmReview?.model || "Qwen3-VL")}</strong><b class="${String(vlmReview?.status || "ERROR").toLowerCase()}">${escapeHtml(vlmReview?.status || "ERROR")}</b></header>
            <div class="roi-inline-value-grid">${reviewValues || '<div><span>复核结果</span><strong>未返回结果</strong></div>'}</div>
            <div class="roi-inline-review-prompt"><span>复核要求</span><p>${escapeHtml(vlmReview?.prompt || reviewConfig.prompt || "未配置复核要求")}</p></div>
            <details><summary>查看 VLM 原始输出</summary><pre>${escapeHtml(JSON.stringify(vlmReview?.parsed || {
              status: vlmReview?.status || "ERROR",
              error: vlmReview?.error || "模型未返回可解析的结构化结果",
            }, null, 2))}</pre></details>
          </section>` : ""}
        </div>
        <footer><span>本规则最终判定</span><strong class="${String(item.status).toLowerCase()}">${escapeHtml(item.status)}</strong><small>${Number(item.elapsed_ms || 0).toFixed(2)} ms</small></footer>
      </article>`;
  }).join("") || '<div class="library-no-results">当前 ROI 没有返回可展示的规则结果</div>';
}

function renderInlineRoiTestError(error, roi) {
  const panel = byId("roiInlineTestPanel");
  panel.hidden = false;
  byId("roiInlineTestTitle").textContent = `${roi.code} 测试失败`;
  byId("roiInlineTestSummary").textContent = "配置仍保留在当前页面，可修正后重新测试。";
  byId("roiInlineTestBadge").className = "result-badge error";
  byId("roiInlineTestBadge").textContent = "ERROR";
  byId("roiInlineTestOverview").innerHTML = `<div><small>错误信息</small><strong>${escapeHtml(error.message)}</strong></div>`;
  byId("roiInlineRuleResults").innerHTML = `<div class="roi-inline-test-error"><strong>测试未完成</strong><p>${escapeHtml(error.message)}</p></div>`;
  byId("roiInlineTestImage").removeAttribute("src");
}

function populateLibraryFilters() {
  const definitions = [
    ["libraryLineFilter", "line_code", "全部拉线"],
    ["libraryMaterialFilter", "material_code", "全部物料"],
    ["libraryProcessFilter", "process_code", "全部工序"],
    ["libraryCameraFilter", "camera_code", "全部相机"],
  ];
  definitions.forEach(([elementId, field, placeholder]) => {
    const select = byId(elementId);
    const current = select.value;
    const values = [...new Set(state.recipes.map((recipe) => recipe[field]).filter(Boolean))]
      .sort((left, right) => String(left).localeCompare(String(right), "zh-CN"));
    select.innerHTML = `<option value="">${placeholder}</option>${values.map((value) =>
      `<option value="${escapeHtml(value)}">${escapeHtml(value)}</option>`).join("")}`;
    if (values.includes(current)) select.value = current;
  });
}

function renderLibraryPagination(totalPages) {
  const container = byId("libraryPagination");
  if (totalPages <= 1) {
    container.innerHTML = "";
    return;
  }
  const pages = [];
  for (let page = 1; page <= totalPages; page += 1) {
    if (page === 1 || page === totalPages || Math.abs(page - state.libraryPage) <= 2) pages.push(page);
  }
  const uniquePages = [...new Set(pages)];
  let previous = 0;
  container.innerHTML = `
    <button type="button" data-library-page="${state.libraryPage - 1}" ${state.libraryPage === 1 ? "disabled" : ""}>上一页</button>
    ${uniquePages.map((page) => {
      const gap = previous && page - previous > 1 ? "<span>…</span>" : "";
      previous = page;
      return `${gap}<button type="button" data-library-page="${page}" class="${page === state.libraryPage ? "active" : ""}">${page}</button>`;
    }).join("")}
    <button type="button" data-library-page="${state.libraryPage + 1}" ${state.libraryPage === totalPages ? "disabled" : ""}>下一页</button>`;
}

function renderLibrary() {
  const query = byId("librarySearch")?.value.trim().toLowerCase() || "";
  const filters = {
    status: byId("libraryStatusFilter")?.value || "",
    line_code: byId("libraryLineFilter")?.value || "",
    material_code: byId("libraryMaterialFilter")?.value || "",
    process_code: byId("libraryProcessFilter")?.value || "",
    camera_code: byId("libraryCameraFilter")?.value || "",
  };
  const sortMode = byId("librarySort")?.value || "UPDATED_DESC";
  const filtered = state.recipes.filter((recipe) => {
    const detail = state.details.get(recipe.id);
    const matchesQuery = !query || JSON.stringify({ ...recipe, ...detail }).toLowerCase().includes(query);
    return matchesQuery && Object.entries(filters).every(([field, value]) => !value || recipe[field] === value);
  });
  filtered.sort((left, right) => {
    if (sortMode === "NAME_ASC") return String(left.name).localeCompare(String(right.name), "zh-CN");
    if (sortMode === "MATERIAL_ASC") return String(left.material_code).localeCompare(String(right.material_code), "zh-CN");
    return String(right.updated_at || right.created_at || right.id).localeCompare(String(left.updated_at || left.created_at || left.id));
  });
  const totalPages = Math.max(1, Math.ceil(filtered.length / state.libraryPageSize));
  state.libraryPage = Math.min(state.libraryPage, totalPages);
  const start = (state.libraryPage - 1) * state.libraryPageSize;
  const visibleRecipes = filtered.slice(start, start + state.libraryPageSize);
  byId("libraryResultsMeta").textContent = filtered.length
    ? `共 ${filtered.length} 个配方，当前显示第 ${start + 1}–${Math.min(start + state.libraryPageSize, filtered.length)} 个`
    : "没有符合当前条件的配方";
  byId("configurationLibrary").innerHTML = filtered.length
    ? visibleRecipes.map((recipe) => `
        <article class="recipe-library-card" data-recipe-id="${recipe.id}">
          <div class="recipe-library-card-top">
            <span class="configuration-symbol">${escapeHtml(recipe.material_code.slice(0, 2) || "VP")}</span>
            <span class="status-pill ${recipe.status === "PUBLISHED" ? "published" : "draft"}">${escapeHtml(recipe.status)}</span>
          </div>
          <h3>${escapeHtml(recipe.name)}</h3>
          <p>${escapeHtml(recipe.code)}</p>
          <div class="recipe-dimensions">
            <span><small>拉线</small><strong>${escapeHtml(recipe.line_code || "-")}</strong></span>
            <span><small>物料</small><strong>${escapeHtml(recipe.material_code || "-")}</strong></span>
            <span><small>工序</small><strong>${escapeHtml(recipe.process_code || "-")}</strong></span>
            <span><small>相机 / 拍照</small><strong>${escapeHtml(recipe.camera_code || "-")} / ${recipe.capture_index}</strong></span>
          </div>
          <div class="recipe-library-metrics">
            <span>${recipe.roi_count} 个检测物体</span>
            <span>${recipe.rule_count} 条规则</span>
          </div>
          <div class="recipe-library-actions">
            <button class="btn btn-outline-primary edit-recipe" type="button">编辑修改</button>
            <button class="btn btn-primary test-recipe" type="button">测试配方</button>
          </div>
        </article>`).join("")
    : '<div class="library-no-results">没有找到匹配的规则配方，请清除筛选或调整关键词。</div>';
  renderLibraryPagination(totalPages);
}

function formatDetectionTime(value) {
  if (!value) return "-";
  const utcValue = /(?:Z|[+-]\d{2}:\d{2})$/.test(value) ? value : `${value}Z`;
  return new Date(utcValue).toLocaleString("zh-CN", { hour12: false });
}

function detectionModelItems(response) {
  return (response.inspection_results || []).flatMap((inspection) =>
    (inspection.image_results || []).flatMap((imageResult) =>
      (imageResult.inspection_items || []).map((item) => ({
        ...item,
        recipeCode: inspection.recipe_code,
        imagePath: imageResult.image_path,
      }))),
  );
}

function conciseModelValue(item) {
  const primary = item.actual?.primary_result || {};
  const details = primary.details || {};
  if (details.text) return details.text;
  if (details.color) return details.color;
  if (details.matched_class) return details.matched_class;
  if (details.top1_similarity != null) return `相似度 ${Number(details.top1_similarity).toFixed(3)}`;
  if (item.score != null) return `分数 ${Number(item.score).toFixed(3)}`;
  return item.message || "无结构化值";
}

function renderRecordModelResults(response) {
  const items = detectionModelItems(response);
  if (!items.length) return '<span class="record-model-empty">无模型明细</span>';
  const problemCount = items.filter((item) => item.status !== "OK").length;
  return `
    <details class="record-model-details">
      <summary><strong>${items.length} 项</strong><span>${problemCount ? `${problemCount} 项异常` : "全部通过"}</span></summary>
      <div class="record-model-list">
        ${items.map((item) => {
          const primary = item.actual?.primary_result || {};
          const review = item.actual?.vlm_review;
          return `<article class="record-model-item ${String(item.status || "ERROR").toLowerCase()}">
            <header><strong>${escapeHtml(item.roi_code || item.item_code || "ROI")}</strong><b>${escapeHtml(item.status || "ERROR")}</b></header>
            <div><span>规则</span><strong>${escapeHtml(item.item_name || item.scene_type || item.capability || "-")}</strong></div>
            <div><span>主模型</span><strong>${escapeHtml(primary.model || item.primary_model || "-")} · ${escapeHtml(primary.status || item.status || "-")}</strong></div>
            <div><span>模型输出</span><strong>${escapeHtml(conciseModelValue(item))}</strong></div>
            <div><span>VLM 复核</span><strong>${review ? `${escapeHtml(review.model || "VLM")} · ${escapeHtml(review.status || "-")}` : "未执行"}</strong></div>
          </article>`;
        }).join("")}
      </div>
    </details>`;
}

function renderDetectionRecords() {
  const records = state.detectionRecords;
  const successCount = records.filter((record) => record.response_code === 0).length;
  byId("detectionRecordsSummary").innerHTML = `
    <span><small>记录数量</small><strong>${records.length}</strong></span>
    <span><small>成功调用</small><strong>${successCount}</strong></span>
    <span><small>失败调用</small><strong>${records.length - successCount}</strong></span>
    <span><small>接口地址</small><strong>/api/detect</strong></span>`;
  byId("detectionRecordsBody").innerHTML = records.length
    ? records.map((record) => {
        const response = record.response_payload || {};
        const requestPayload = JSON.stringify(record.request_payload || {}, null, 2);
        const responsePayload = JSON.stringify(response, null, 2);
        return `
          <tr>
            <td>${escapeHtml(formatDetectionTime(record.called_at))}</td>
            <td><code>${escapeHtml(record.caller_ip)}</code></td>
            <td><strong>${escapeHtml(record.sn)}</strong></td>
            <td><span class="call-status ${record.response_code === 0 ? "success" : "failed"}">${record.response_code} · ${escapeHtml(record.call_status)}</span></td>
            <td><span class="result-badge ${(response.result || "error").toLowerCase()}">${escapeHtml(response.result || "ERROR")}</span></td>
            <td>${renderRecordModelResults(response)}</td>
            <td>${record.elapsed_ms == null ? "-" : `${record.elapsed_ms} ms`}</td>
            <td>
              <details class="call-payload-details">
                <summary>查看参数</summary>
                <div><strong>调用参数</strong><pre>${escapeHtml(requestPayload)}</pre></div>
                <div><strong>返回参数</strong><pre>${escapeHtml(responsePayload)}</pre></div>
              </details>
            </td>
          </tr>`;
      }).join("")
    : '<tr><td colspan="8" class="records-empty">暂无第三方检测调用记录</td></tr>';
}

async function loadDetectionRecords() {
  byId("detectionRecordsBody").innerHTML =
    '<tr><td colspan="8" class="records-empty">正在加载检测记录…</td></tr>';
  try {
    state.detectionRecords = await request(`${api}/inspection/call-records?limit=200`);
    renderDetectionRecords();
  } catch (error) {
    byId("detectionRecordsBody").innerHTML =
      `<tr><td colspan="8" class="records-empty error">${escapeHtml(error.message)}</td></tr>`;
  }
}

const modelServiceStatusLabels = {
  READY: "运行正常",
  STARTING: "正在启动",
  STOPPED: "已停止",
  ERROR: "运行异常",
};

function renderModelServices(payload) {
  const summary = payload.summary || {};
  byId("modelServicesSummary").innerHTML = [
    ["服务总数", summary.total || 0],
    ["运行正常", summary.ready || 0],
    ["正在启动", summary.starting || 0],
    ["异常 / 已停止", Number(summary.problem || 0) + Number(summary.stopped || 0)],
  ].map(([label, value]) => `
    <div class="model-service-summary-card"><small>${label}</small><strong>${value}</strong></div>`).join("");

  const ready = Number(summary.ready || 0);
  const total = Number(summary.total || 0);
  const chip = byId("serviceSummaryChip");
  chip.textContent = total && ready === total ? `全部 ${total} 个模型服务正常` : `${ready}/${total} 个模型服务正常`;

  byId("modelServicesList").innerHTML = state.modelServices.map((service) => {
    const status = String(service.status || "ERROR").toUpperCase();
    const canStart = ["STOPPED", "ERROR"].includes(status)
      && !service.pid
      && service.script_exists
      && service.python_exists;
    const canStop = Boolean(service.pid);
    const environmentProblem = !service.script_exists
      ? "启动脚本不存在"
      : !service.python_exists ? "模型运行环境不存在" : "";
    return `
      <article class="model-service-card ${status.toLowerCase()}" data-model-service="${escapeHtml(service.code)}">
        <header>
          <div><strong>${escapeHtml(service.name)}</strong><small>${escapeHtml(service.category)} · ${escapeHtml(service.code)}</small></div>
          <span class="model-service-status ${status.toLowerCase()}">${escapeHtml(modelServiceStatusLabels[status] || status)}</span>
        </header>
        <div class="model-service-address">
          <div><span>服务 IP</span><strong>${escapeHtml(service.host)}</strong></div>
          <div><span>端口</span><strong>${escapeHtml(service.port)}</strong></div>
          <div><span>完整地址</span><strong>${escapeHtml(service.url)}</strong></div>
        </div>
        <div class="model-service-process">
          <div><span>进程 PID</span><strong>${escapeHtml(service.pid || "-")}</strong></div>
          <div><span>管理方式</span><strong>${service.managed ? "平台启动" : service.pid ? "外部启动" : "未运行"}</strong></div>
        </div>
        ${(environmentProblem || service.last_error) ? `<pre class="model-service-error-preview">${escapeHtml(environmentProblem || service.last_error)}</pre>` : ""}
        <footer>
          <button class="btn btn-sm btn-light model-service-logs" type="button" data-service-code="${escapeHtml(service.code)}">查看日志</button>
          <button class="btn btn-sm btn-outline-danger model-service-stop" type="button" data-service-code="${escapeHtml(service.code)}" ${canStop ? "" : "disabled"}>停止</button>
          <button class="btn btn-sm btn-primary model-service-start" type="button" data-service-code="${escapeHtml(service.code)}" ${canStart ? "" : "disabled"}>启动</button>
        </footer>
      </article>`;
  }).join("") || '<div class="library-no-results">暂无模型服务配置</div>';
}

async function loadModelServices(silent = false) {
  const refreshButton = byId("refreshModelServices");
  if (!silent) {
    refreshButton.disabled = true;
    refreshButton.textContent = "正在检查…";
  }
  try {
    const payload = await request(`${api}/model-services`);
    state.modelServices = payload.services || [];
    renderModelServices(payload);
  } catch (error) {
    byId("modelServicesList").innerHTML = `<div class="library-no-results">${escapeHtml(error.message)}</div>`;
    byId("serviceSummaryChip").textContent = "模型服务状态不可用";
    if (!silent) notify(`模型服务状态读取失败：${error.message}`, "danger", false);
  } finally {
    refreshButton.disabled = false;
    refreshButton.textContent = "刷新状态";
  }
}

async function controlModelService(code, action) {
  const service = state.modelServices.find((item) => item.code === code);
  try {
    const result = await request(`${api}/model-services/${encodeURIComponent(code)}/${action}`, { method: "POST" });
    notify(result.message, "success", false);
    await loadModelServices(true);
    if (action === "start") {
      window.setTimeout(() => loadModelServices(true), 3500);
    }
  } catch (error) {
    notify(`${service?.name || code}${action === "start" ? "启动" : "停止"}失败：${error.message}`, "danger", false);
    await loadModelServices(true);
  }
}

function scrollModelLogsToLatest() {
  ["modelServiceCalls", "modelServiceStdout", "modelServiceStderr"].forEach((id) => {
    const element = byId(id);
    element.scrollTop = element.scrollHeight;
  });
}

function stopModelServiceLogRefresh() {
  if (state.modelServiceLogTimer) window.clearInterval(state.modelServiceLogTimer);
  state.modelServiceLogTimer = null;
  state.activeModelServiceLogCode = null;
}

function startModelServiceLogRefresh(code) {
  stopModelServiceLogRefresh();
  state.activeModelServiceLogCode = code;
  state.modelServiceLogTimer = window.setInterval(() => {
    if (!byId("modelServiceLogPanel").hidden && state.activeModelServiceLogCode === code) {
      showModelServiceLogs(code, true);
    }
  }, 3000);
}

async function showModelServiceLogs(code, silent = false) {
  try {
    const logs = await request(`${api}/model-services/${encodeURIComponent(code)}/logs?lines=300`);
    byId("modelServiceLogTitle").textContent = logs.name;
    byId("modelServiceLogPaths").textContent = `调用日志：${logs.call_log_path}　标准输出：${logs.stdout_path}　错误输出：${logs.stderr_path}`;
    byId("modelServiceCalls").textContent = logs.calls || "暂无测试调用记录";
    byId("modelServiceStdout").textContent = logs.stdout || "暂无标准输出日志";
    byId("modelServiceStderr").textContent = logs.stderr || "暂无错误日志";
    byId("modelServiceLogPanel").hidden = false;
    scrollModelLogsToLatest();
    if (!silent) {
      startModelServiceLogRefresh(code);
      byId("modelServiceLogPanel").scrollIntoView({ behavior: "smooth", block: "start" });
    }
  } catch (error) {
    if (!silent) notify(`读取模型服务日志失败：${error.message}`, "danger", false);
  }
}

function switchView(viewId) {
  if (viewId !== "servicesView" && state.modelServiceLogTimer) stopModelServiceLogRefresh();
  document.querySelectorAll(".workspace-switch").forEach((button) => {
    button.classList.toggle("active", button.dataset.view === viewId);
  });
  document.querySelectorAll(".workspace-view").forEach((view) => {
    view.classList.toggle("active", view.id === viewId);
  });
}

function openTest(recipeId) {
  state.testRecipe = state.details.get(recipeId);
  state.testFile = null;
  state.testDraft = null;
  byId("testRecipeName").textContent = state.testRecipe.name;
  byId("testFile").value = "";
  byId("testImageComparison").hidden = true;
  byId("testActualImageLabel").textContent = "实测图";
  const referenceImage = byId("testReferenceImage");
  const referenceEmpty = byId("testReferenceEmpty");
  if (state.testRecipe.base_image_url) {
    referenceImage.src = `${state.testRecipe.base_image_url}?v=${Date.now()}`;
    referenceImage.hidden = false;
    referenceEmpty.hidden = true;
  } else {
    referenceImage.removeAttribute("src");
    referenceImage.hidden = true;
    referenceEmpty.hidden = false;
  }
  byId("testUploadStage").hidden = false;
  byId("runRecipeTest").disabled = true;
  byId("testOverview").innerHTML = `
    <div><small>检测状态</small><strong>等待上传图片</strong></div>
    <span class="result-badge pending">WAITING</span>`;
  byId("testObjectResults").innerHTML =
    '<div class="no-object-selected"><span>◎</span><strong>检测明细将在这里展示</strong><p>每个检测物体及其多条规则会分别显示结果。</p></div>';
  switchView("testView");
}

function previewTestFile(file) {
  if (!file) return;
  state.testFile = file;
  const preview = byId("testPreviewImage");
  preview.src = URL.createObjectURL(file);
  byId("testActualImageLabel").textContent = "实测图（检测前）";
  byId("testImageComparison").hidden = false;
  byId("testUploadStage").hidden = true;
  byId("runRecipeTest").disabled = false;
}

function renderTestResult(result) {
  const items = result.image_results?.[0]?.inspection_items || [];
  const grouped = items.reduce((groups, item) => {
    (groups[item.roi_code] ||= []).push(item);
    return groups;
  }, {});
  byId("testOverview").innerHTML = `
    <div><small>检测状态</small><strong>${result.result === "OK" ? "全部规则通过" : "发现异常规则"}</strong></div>
    <span class="result-badge ${result.result.toLowerCase()}">${escapeHtml(result.result)}</span>
    <div><small>总耗时</small><strong>${result.elapsed_ms} ms</strong></div>`;
  byId("testObjectResults").innerHTML = Object.entries(grouped).map(([roiCode, roiItems]) => {
    const roi = state.testRecipe.rois.find((item) => item.code === roiCode);
    const standardRoiUrl = roi?.reference?.image_url || "";
    const actualRoiUrl = roiItems.find((item) => item.roi_image_url)?.roi_image_url || "";
    const status = roiItems.some((item) => item.status === "ERROR")
      ? "ERROR"
      : roiItems.some((item) => item.status === "NG") ? "NG" : "OK";
    return `
      <section class="test-object-card">
        <div class="test-object-card-heading">
          <div><small>${escapeHtml(roiCode)}</small><strong>${escapeHtml(roi?.name || roiCode)}</strong></div>
          <span class="result-badge ${status.toLowerCase()}">${status}</span>
        </div>
        <div class="test-roi-image-comparison">
          <figure>
            <figcaption>标准 ROI 图</figcaption>
            ${standardRoiUrl
              ? `<img src="${escapeHtml(standardRoiUrl)}?v=${Date.now()}" alt="${escapeHtml(roiCode)} 标准 ROI 图">`
              : '<div class="comparison-image-empty">当前区域还没有标准图</div>'}
          </figure>
          <figure>
            <figcaption>实测 ROI 图</figcaption>
            ${actualRoiUrl
              ? `<img src="${escapeHtml(actualRoiUrl)}?v=${Date.now()}" alt="${escapeHtml(roiCode)} 实测 ROI 图">`
              : '<div class="comparison-image-empty">未返回实测ROI图</div>'}
          </figure>
        </div>
        <div class="test-rule-details">
          ${roiItems.map((item) => {
            const actual = item.actual || {};
            const primary = actual.primary_result || {
              model: item.primary_model || item.capability,
              status: actual.primary_status || item.status,
              score: item.score,
              message: item.message,
            };
            const review = actual.vlm_review;
            const reviewEnabled = Boolean(item.vlm_review_enabled);
            const reviewReason = review?.parsed?.reason || review?.error || "未执行VLM复核";
            const reviewPrompt = review?.prompt || "未配置复核内容";
            const reviewDetails = review?.parsed
              ? JSON.stringify(review.parsed, null, 2)
              : JSON.stringify({ error: review?.error || "未执行" }, null, 2);
            return `
            <div class="test-rule-row">
              <span class="rule-result-icon ${item.status.toLowerCase()}">${item.status === "OK" ? "✓" : "!"}</span>
              <div class="test-rule-content">
                <strong>${escapeHtml(item.item_name)}</strong>
                <small>${escapeHtml(sceneMeta[item.scene_type]?.label || item.inspection_type || "检测规则")}</small>
                <div class="model-result-comparison">
                  <div class="model-result-card primary">
                    <span>主模型 · ${escapeHtml(primary.model || "未指定")}</span>
                    <b class="${String(primary.status || "ERROR").toLowerCase()}">${escapeHtml(primary.status || "ERROR")}</b>
                    <small>${primary.score == null ? escapeHtml(primary.message || item.message) : `置信度 / 分数 ${Number(primary.score).toFixed(4)}`}</small>
                  </div>
                  ${reviewEnabled ? `<div class="model-result-card review ${review ? "executed" : "skipped"}">
                    <span>VLM复核 · ${escapeHtml(review?.model || "Qwen3-VL")}</span>
                    <b class="${String(review?.status || "SKIPPED").toLowerCase()}">${escapeHtml(review?.status || "未执行")}</b>
                    <small><strong>复核内容：</strong>${escapeHtml(reviewPrompt)}</small>
                    <small><strong>复核说明：</strong>${escapeHtml(reviewReason)}</small>
                    <pre>${escapeHtml(reviewDetails)}</pre>
                  </div>` : ""}
                </div>
              </div>
              <b>最终 ${escapeHtml(item.status)}</b>
            </div>`;
          }).join("")}
        </div>
      </section>`;
  }).join("") || '<div class="library-no-results">该配方没有可执行规则</div>';
  byId("testPreviewImage").src = `/results/${result.request_id}/result_1.jpg?v=${Date.now()}`;
  byId("testActualImageLabel").textContent = "实测结果图";
  byId("testImageComparison").hidden = false;
}

async function runTest(roiId = null) {
  if (!state.testRecipe || !state.testFile) return;
  const draft = state.testDraft;
  const targetRoiId = roiId || draft?.roiId || null;
  const data = new FormData();
  data.append("recipe_id", state.testRecipe.id);
  if (targetRoiId) data.append("roi_id", targetRoiId);
  if (draft) {
    data.append("draft_rules", JSON.stringify(draft.rules));
    data.append("review_config", JSON.stringify(draft.review));
  }
  data.append("file", state.testFile);
  byId("runRecipeTest").disabled = true;
  byId("runRecipeTest").textContent = "正在执行检测…";
  try {
    const result = await request(`${api}/inspection/test`, { method: "POST", body: data });
    renderTestResult(result);
  } catch (error) {
    byId("testOverview").innerHTML = `
      <div><small>检测状态</small><strong>${escapeHtml(error.message)}</strong></div>
      <span class="result-badge error">ERROR</span>`;
  } finally {
    byId("runRecipeTest").disabled = false;
    byId("runRecipeTest").textContent = "重新执行全部规则";
  }
}

document.querySelectorAll(".recipe-name-source").forEach((input) => {
  input.addEventListener("input", updateGeneratedName);
});

document.querySelectorAll(".workspace-switch").forEach((button) => {
  button.addEventListener("click", async () => {
    switchView(button.dataset.view);
    if (button.dataset.view === "referenceLibraryView") await reloadReferenceLibrary();
    if (button.dataset.view === "recordsView") await loadDetectionRecords();
    if (button.dataset.view === "servicesView") await loadModelServices();
  });
});

byId("newRecipe").addEventListener("click", resetEditor);
byId("saveRecipe").addEventListener("click", saveRecipe);
byId("autoDiscoverButton").addEventListener("click", discoverObjects);
byId("baseImageInput").addEventListener("change", (event) => uploadBaseImage(event.target.files[0]));
byId("emptyImageInput").addEventListener("change", (event) => uploadBaseImage(event.target.files[0]));
byId("imageStage").addEventListener("dragover", (event) => event.preventDefault());
byId("imageStage").addEventListener("drop", (event) => {
  event.preventDefault();
  uploadBaseImage(event.dataTransfer.files[0]);
});
byId("zoomOutButton")?.addEventListener("click", () => setImageScale(state.imageView.scale / 1.2));
byId("zoomInButton")?.addEventListener("click", () => setImageScale(state.imageView.scale * 1.2));
byId("resetZoomButton")?.addEventListener("click", resetImageView);
byId("togglePanButton")?.addEventListener("click", () => {
  state.imageView.panMode = !state.imageView.panMode;
  applyImageTransform();
});
imageStage.addEventListener("wheel", (event) => {
  if (!state.recipe?.base_image_url) return;
  event.preventDefault();
  const factor = Math.exp(-event.deltaY * 0.0015);
  setImageScale(state.imageView.scale * factor, event.clientX, event.clientY);
}, { passive: false });
imageStage.addEventListener("dblclick", (event) => {
  const surfaceBounds = imageSurface.getBoundingClientRect();
  if (
    event.clientX >= surfaceBounds.left
    && event.clientX <= surfaceBounds.right
    && event.clientY >= surfaceBounds.top
    && event.clientY <= surfaceBounds.bottom
  ) resetImageView();
});
document.addEventListener("keydown", (event) => {
  if (event.code !== "Space" || ["INPUT", "TEXTAREA", "SELECT"].includes(event.target.tagName)) return;
  event.preventDefault();
  state.imageView.spacePressed = true;
  applyImageTransform();
});
document.addEventListener("keyup", (event) => {
  if (event.code !== "Space") return;
  state.imageView.spacePressed = false;
  applyImageTransform();
});

byId("configuredObjectList").addEventListener("click", async (event) => {
  const card = event.target.closest("[data-detail-roi]");
  if (!card) return;
  if (event.target.closest(".configured-object-rule-details")) return;
  const roiId = Number(card.dataset.detailRoi);
  if (event.target.closest(".edit-object")) {
    openObjectModal(roiId);
    return;
  }
  if (event.target.closest(".delete-object")) {
    const roi = state.recipe.rois.find((item) => item.id === roiId);
    if (!window.confirm(`确定删除“${roi?.name || "该检测区域"}”及其全部规则吗？`)) return;
    await request(`${api}/configuration/rois/${roiId}`, { method: "DELETE" });
    await loadRecipe(state.recipe.id);
    notify("检测区域及其规则已删除，图片上的对应框已同步移除", "success", false);
    return;
  }
  selectRoi(roiId);
});

byId("candidateObjectList").addEventListener("click", async (event) => {
  const card = event.target.closest("[data-candidate-id]");
  if (!card) return;
  const candidate = state.discoveryCandidates.find(
    (item) => item.candidate_id === card.dataset.candidateId,
  );
  if (!candidate) return;
  if (event.target.closest(".delete-candidate")) {
    deleteDiscoveryCandidate(candidate.candidate_id);
    return;
  }
  if (event.target.closest(".confirm-candidate")) {
    await confirmCandidates([candidate]);
    return;
  }
  selectCandidate(candidate.candidate_id);
});

byId("confirmAllCandidates").addEventListener("click", async () => {
  const recommended = state.discoveryCandidates.filter(
    (candidate) => candidate.batch_confirmable !== false && (
      candidate.review_status === "RECOMMENDED"
      || (candidate.confidence || 0) >= 0.40
    ),
  );
  if (!recommended.length) {
    notify("当前没有建议批量确认的候选框，请逐个检查并确认。", "warning", false);
    return;
  }
  await confirmCandidates(recommended);
});

byId("clearCandidates").addEventListener("click", () => {
  state.discoveryCandidates = [];
  state.harnessSegments = [];
  state.harnessSegmentation = null;
  state.selectedCandidateId = null;
  renderDiscoveryCandidates();
  drawCanvas();
  notify("AI 候选框已清空，已确认的正式检测对象不会受影响。", "info", false);
});

byId("objectConfigModal").querySelector(".btn-close").addEventListener("click", closeObjectModal);
byId("addRuleRow").addEventListener("click", addRuleRow);
byId("saveRoiRules").addEventListener("click", () => saveRoiRules());
byId("testRoiRules").addEventListener("click", testCurrentRoiRules);
byId("roiObjectType").addEventListener("change", () => refreshVlmPrompt());
byId("regenerateVlmPrompt").addEventListener("click", () => refreshVlmPrompt(true));
byId("vlmReviewPrompt").addEventListener("input", () => {
  state.vlmPromptDirty = true;
});
byId("vlmReviewEnabled").addEventListener("change", () => {
  if (byId("vlmReviewEnabled").checked) refreshVlmPrompt();
  syncVlmReviewMode();
});
byId("vlmReviewMode").addEventListener("change", syncVlmReviewMode);
byId("ruleRows").addEventListener("change", async (event) => {
  const row = event.target.closest("[data-rule-index]");
  if (!row || !event.target.classList.contains("rule-row-scene")) return;
  const index = Number(row.dataset.ruleIndex);
  const sceneCode = event.target.value;
  const scene = sceneMeta[sceneCode] || sceneMeta.OBJECT_EXISTENCE;
  const defaults = {
    EXISTENCE: "0.9",
    COLOR: "",
    TEXT: "",
  };
  state.draftRules[index].scene = sceneCode;
  state.draftRules[index].type = scene.ruleType;
  state.draftRules[index].value = defaults[scene.ruleType];
  renderRuleRows();
  refreshVlmPrompt();
  if (scene.ruleType === "COLOR") await detectColorForRule(index);
});
byId("ruleRows").addEventListener("input", (event) => {
  const row = event.target.closest("[data-rule-index]");
  if (!row || !event.target.classList.contains("rule-row-value")) return;
  state.draftRules[Number(row.dataset.ruleIndex)].value = event.target.value;
  refreshVlmPrompt();
});
byId("ruleRows").addEventListener("click", async (event) => {
  const colorButton = event.target.closest(".detect-rule-color");
  if (colorButton) {
    await detectColorForRule(Number(colorButton.dataset.colorRuleIndex));
    return;
  }
  const button = event.target.closest(".remove-rule-row");
  if (!button) return;
  const row = button.closest("[data-rule-index]");
  state.draftRules.splice(Number(row.dataset.ruleIndex), 1);
  renderRuleRows();
  refreshVlmPrompt();
});

function resetLibraryPageAndRender() {
  state.libraryPage = 1;
  renderLibrary();
}

byId("librarySearch").addEventListener("input", resetLibraryPageAndRender);
["libraryStatusFilter", "libraryLineFilter", "libraryMaterialFilter", "libraryProcessFilter", "libraryCameraFilter", "librarySort"]
  .forEach((elementId) => byId(elementId).addEventListener("change", resetLibraryPageAndRender));
byId("clearLibraryFilters").addEventListener("click", () => {
  byId("librarySearch").value = "";
  ["libraryStatusFilter", "libraryLineFilter", "libraryMaterialFilter", "libraryProcessFilter", "libraryCameraFilter"]
    .forEach((elementId) => { byId(elementId).value = ""; });
  byId("librarySort").value = "UPDATED_DESC";
  resetLibraryPageAndRender();
});
byId("libraryPagination").addEventListener("click", (event) => {
  const button = event.target.closest("[data-library-page]");
  if (!button || button.disabled) return;
  state.libraryPage = Number(button.dataset.libraryPage);
  renderLibrary();
  byId("libraryView").scrollIntoView({ behavior: "smooth", block: "start" });
});
byId("referenceCandidateStatusFilter")?.addEventListener("change", renderReferenceCandidates);
byId("referenceCandidateGrid")?.addEventListener("click", async (event) => {
  const promoteButton = event.target.closest("[data-candidate-promote]");
  const rejectButton = event.target.closest("[data-candidate-reject]");
  const button = promoteButton || rejectButton;
  if (!button) return;
  button.disabled = true;
  try {
    if (promoteButton) {
      const result = await updateReferenceCandidate(Number(promoteButton.dataset.candidatePromote), "promote");
      notify(
        result.skipped ? result.reason : "候选图片已加入正式基准；达到上限时只会软停用重复旧基准",
        result.skipped ? "info" : "success",
        false,
      );
    } else {
      await updateReferenceCandidate(Number(rejectButton.dataset.candidateReject), "reject");
      notify("候选图片已拒绝，不会进入正式基准", "success", false);
    }
  } catch (error) {
    notify(error.message, "danger", false);
  } finally {
    button.disabled = false;
  }
});
byId("refreshReferenceLibrary").addEventListener("click", async () => {
  try {
    await reloadReferenceLibrary();
    notify("候选基准图已刷新", "success", false);
  } catch (error) {
    notify(error.message, "danger", false);
  }
});
byId("refreshDetectionRecords").addEventListener("click", loadDetectionRecords);
byId("refreshModelServices").addEventListener("click", () => loadModelServices());
byId("closeModelServiceLogs").addEventListener("click", () => {
  byId("modelServiceLogPanel").hidden = true;
  stopModelServiceLogRefresh();
});
byId("modelServicesList").addEventListener("click", async (event) => {
  const button = event.target.closest("[data-service-code]");
  if (!button) return;
  const code = button.dataset.serviceCode;
  if (button.classList.contains("model-service-start")) await controlModelService(code, "start");
  if (button.classList.contains("model-service-stop")) await controlModelService(code, "stop");
  if (button.classList.contains("model-service-logs")) await showModelServiceLogs(code);
});
byId("configurationLibrary").addEventListener("click", async (event) => {
  const card = event.target.closest("[data-recipe-id]");
  if (!card) return;
  const recipeId = Number(card.dataset.recipeId);
  if (event.target.closest(".edit-recipe")) {
    switchView("editorView");
    await loadRecipe(recipeId);
  } else if (event.target.closest(".test-recipe")) {
    openTest(recipeId);
  }
});

byId("backToLibrary").addEventListener("click", () => switchView("libraryView"));
byId("editTestRecipe").addEventListener("click", async () => {
  if (!state.testRecipe) return;
  switchView("editorView");
  await loadRecipe(state.testRecipe.id);
});
byId("testFile").addEventListener("change", (event) => previewTestFile(event.target.files[0]));
byId("runRecipeTest").addEventListener("click", () => runTest());

loadData()
  .then(async () => {
    if (state.recipes.length) await loadRecipe(state.recipes[0].id);
    else resetEditor();
  })
  .catch((error) => notify(error.message, "danger"));

syncVlmReviewMode();
loadModelServices(true);
