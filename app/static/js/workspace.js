const api = "/api/v1";
const state = {
  products: [],
  stations: [],
  recipes: [],
  references: [],
  referenceObjectTypes: [],
  publishedScenarios: [],
  roiScenarioBindings: new Map(),
  details: new Map(),
  recipe: null,
  worldScene: null,
  selectedRoiId: null,
  drawMode: "ROI",
  pendingRect: null,
  drawing: false,
  startPoint: null,
  activePointerId: null,
  pointerRoi: null,
  interactionMode: null,
  workingRect: null,
  originalRect: null,
  savingRoi: false,
  draftRules: [],
  vlmPromptDirty: false,
  detectionRecords: [],
  inspectionReports: null,
  reportSceneSearch: "",
  reportSceneSort: { key: "total", direction: "desc" },
  libraryPage: 1,
  libraryPageSize: 12,
  recipeHistory: { sourceRecipeId: null, versions: [] },
  editorReadOnly: false,
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
    projectName: values.project_name.trim(),
    lineCode: normalizeCode(values.line_code),
    materialCode: normalizeCode(values.material_code),
    processCode: normalizeCode(values.process_code),
    cameraCode: normalizeCode(values.camera_code),
    captureIndex: Math.max(1, Number(values.capture_index || 1)),
    version: String(values.version || "").trim() || "1.0",
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
      ? `${fields.projectName ? `${fields.projectName} · ` : ""}${fields.lineCode} · ${fields.materialCode} · ${fields.processCode} · ${fields.cameraCode} · 第${fields.captureIndex}次拍照`
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
    state.publishedScenarios,
  ] = await Promise.all([
    request(`${api}/configuration/products`),
    request(`${api}/configuration/stations`),
    request(`${api}/configuration/recipes`),
    request(`${api}/configuration/reference-groups`),
    request(`${api}/configuration/reference-object-types`),
    request(`${api}/scenarios/published`),
  ]);
  const entries = await Promise.all(
    state.recipes.map(async (recipe) => [
      recipe.id,
      await request(`${api}/configuration/recipes/${recipe.id}`),
    ]),
  );
  state.details = new Map(entries);
  fillReferenceSelects();
  fillRoiScenarioSelect();
  populateLibraryFilters();
  renderLibrary();
  if (preferredRecipeId) await loadRecipe(preferredRecipeId);
}

function sceneInputsForVersion(versionId) {
  return state.publishedScenarios.find(
    (item) => Number(item.scenario_version_id) === Number(versionId),
  )?.input_fields || [];
}

function roiScenarioInputRowMarkup(fields, key = "", value = "") {
  const options = [
    '<option value="">请选择字段</option>',
    ...fields.map((field) => `<option value="${escapeHtml(field.name)}" ${field.name === key ? "selected" : ""}>${escapeHtml(field.label)}（${escapeHtml(field.name)}）</option>`),
  ].join("");
  return `<div class="roi-scenario-input-row">
    <select class="form-select" data-roi-scenario-input-key>${options}</select>
    <input class="form-control" data-roi-scenario-input-value value="${escapeHtml(value ?? "")}" placeholder="输入该字段的校验值">
    <button class="btn btn-sm btn-outline-danger" type="button" data-remove-roi-scenario-input aria-label="删除字段">×</button>
  </div>`;
}

function renderRoiScenarioInputRows(versionId, mapping = {}) {
  const rows = byId("roiScenarioInputRows");
  const hint = byId("roiScenarioInputHint");
  const addButton = byId("addRoiScenarioInput");
  if (!rows || !hint || !addButton) return;
  const fields = sceneInputsForVersion(versionId);
  const entries = Object.entries(mapping || {});
  rows.innerHTML = fields.length
    ? (entries.length
      ? entries.map(([key, value]) => roiScenarioInputRowMarkup(fields, key, value)).join("")
      : '<div class="roi-scenario-input-empty">该场景声明了多个可配置字段；需要时点击“添加字段”逐项填写。</div>')
    : '<div class="roi-scenario-input-empty">该场景只接收系统图片或未声明业务字段，无需填写校验值。</div>';
  addButton.disabled = !fields.length || state.editorReadOnly;
  hint.textContent = fields.length
    ? "图片字段由系统自动传入。可添加多个字段；同一字段只能配置一次，场景内可通过 {{ input.字段名 }} 引用。"
    : "该场景只接收系统图片或未声明业务字段，无需填写校验值。";
}

function collectRoiScenarioInputMapping() {
  const mapping = {};
  const rows = [...document.querySelectorAll("#roiScenarioInputRows .roi-scenario-input-row")];
  for (const row of rows) {
    const key = row.querySelector("[data-roi-scenario-input-key]")?.value.trim();
    const value = row.querySelector("[data-roi-scenario-input-value]")?.value.trim();
    if (!key && !value) continue;
    if (!key || !value) throw new Error("每个场景字段都必须同时选择字段并填写校验值。");
    if (Object.prototype.hasOwnProperty.call(mapping, key)) {
      throw new Error(`场景字段“${key}”只能配置一次。`);
    }
    mapping[key] = value;
  }
  return mapping;
}

function fillRoiScenarioSelect(selectedVersionId = null) {
  const select = byId("roiScenarioVersion");
  if (!select) return;
  const current = selectedVersionId ?? select.value;
  select.innerHTML = [
    '<option value="">请选择已发布场景</option>',
    ...state.publishedScenarios.map((item) => `
      <option value="${item.scenario_version_id}" ${Number(current) === item.scenario_version_id ? "selected" : ""}>
        ${escapeHtml(item.scenario_name)} · V${escapeHtml(item.version)}
      </option>`),
  ].join("");
  select.disabled = !state.publishedScenarios.length;
  byId("roiScenarioBindingHint").textContent = state.publishedScenarios.length
    ? "只能选择已发布场景；配方发布后，生产检测会执行这里选择的场景。"
    : "还没有已发布场景，请先到“场景管理”创建并发布。";
  renderRoiScenarioInputRows(select.value);
}

async function refreshRoiScenarioBinding(roi) {
  if (!roi) return;
  fillRoiScenarioSelect(state.roiScenarioBindings.get(roi.id)?.scenario_version_id || null);
  try {
    const binding = await request(`${api}/scenarios/rois/${roi.id}/binding`);
    state.roiScenarioBindings.set(roi.id, binding);
    if (selectedRoi()?.id === roi.id) {
      fillRoiScenarioSelect(binding.scenario_version_id || null);
      renderRoiScenarioInputRows(
        binding.scenario_version_id,
        binding.input_mapping_json || {},
      );
    }
  } catch (error) {
    console.warn("读取 ROI 场景关联失败", error);
  }
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
  form.elements.project_name.value = recipe?.project_name || "";
  form.elements.line_code.value = recipe?.line_code || "";
  form.elements.material_code.value = recipe?.material_code || "";
  form.elements.process_code.value = recipe?.process_code || "";
  form.elements.camera_code.value = recipe?.camera_code || "";
  form.elements.capture_index.value = recipe?.capture_index || 1;
  form.elements.version.value = recipe?.display_version || "草稿（首次发布 V1）";
  updateGeneratedName();
}

function setRecipeStatus(status, recipe = null) {
  const isPublished = status === "PUBLISHED";
  const isSaved = status === "SAVED";
  const modeSuffix = state.editorReadOnly ? " · 只读详情" : "";
  byId("recipeStatus").className = `status-pill ${isPublished ? "published" : "draft"}`;
  byId("recipeStatus").innerHTML = `
    <span class="status-dot ${isPublished ? "published" : "draft"}"></span>
    ${isPublished ? "已发布，可供检测调用" : isSaved ? "已保存，尚未发布" : recipe ? "草稿编辑中" : "未保存"}${modeSuffix}`;
}

function applyEditorAccessMode() {
  const readOnly = Boolean(state.editorReadOnly);
  byId("editorView")?.classList.toggle("recipe-detail-readonly", readOnly);
  byId("recipeEditorModeHint").hidden = !readOnly;
  byId("recipeForm")?.querySelectorAll("input, select, textarea").forEach((field) => {
    field.disabled = readOnly;
  });
  ["saveRecipe", "publishRecipe", "baseImageInput", "emptyImageInput"].forEach((id) => {
    const control = byId(id);
    if (control) control.disabled = readOnly;
  });
  ["selectRoiDrawMode", "selectFeatureAnchorDrawMode"].forEach((id) => {
    const control = byId(id);
    if (control) control.disabled = readOnly || !state.recipe?.base_image_url;
  });
  ["baseImageUploadLabel", "emptyImageUploadLabel"].forEach((id) => {
    byId(id)?.classList.toggle("disabled", readOnly);
  });
}

function resetEditor() {
  state.editorReadOnly = false;
  state.recipe = null;
  state.worldScene = null;
  state.selectedRoiId = null;
  state.pendingRect = null;
  state.workingRect = null;
  state.interactionMode = null;
  state.drawMode = "ROI";
  byId("recipeForm").reset();
  byId("recipeForm").elements.capture_index.value = "1";
  byId("recipeForm").elements.version.value = "草稿（首次发布 V1）";
  populateRecipeForm(null);
  setRecipeStatus("DRAFT");
  baseImage.removeAttribute("src");
  baseImage.removeAttribute("data-source-url");
  baseImage.style.display = "none";
  imageSurface.hidden = true;
  byId("imageNavigationHint").hidden = true;
  byId("emptyStage").style.display = "grid";
  byId("selectRoiDrawMode").disabled = true;
  byId("selectFeatureAnchorDrawMode").disabled = true;
  byId("selectRoiDrawMode").classList.add("active");
  byId("selectFeatureAnchorDrawMode").classList.remove("active");
  clearCanvas();
  renderConfiguredObjects();
  applyEditorAccessMode();
}

async function loadRecipe(recipeId) {
  const recipeUrl = `${api}/configuration/recipes/${recipeId}`;
  state.recipe = await request(recipeUrl);
  state.worldScene = await request(`${api}/world/recipes/${recipeId}/scene`);
  state.recipe = await request(recipeUrl);
  state.details.set(state.recipe.id, state.recipe);
  state.selectedRoiId = null;
  state.pendingRect = null;
  state.workingRect = null;
  state.interactionMode = null;
  state.drawMode = "ROI";
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
    byId("selectRoiDrawMode").disabled = false;
    byId("selectFeatureAnchorDrawMode").disabled = false;
    byId("selectRoiDrawMode").classList.add("active");
    byId("selectFeatureAnchorDrawMode").classList.remove("active");
    if (!imageChanged && baseImage.complete && baseImage.naturalWidth) syncCanvas(false);
  } else {
    baseImage.removeAttribute("src");
    baseImage.removeAttribute("data-source-url");
    baseImage.style.display = "none";
    imageSurface.hidden = true;
    byId("imageNavigationHint").hidden = true;
    byId("emptyStage").style.display = "grid";
    byId("selectRoiDrawMode").disabled = true;
    byId("selectFeatureAnchorDrawMode").disabled = true;
    clearCanvas();
  }
  renderConfiguredObjects();
  applyEditorAccessMode();
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
    project_name: fields.projectName || null,
    product_id: product.id,
    station_id: station.id,
    line_code: fields.lineCode,
    material_code: fields.materialCode,
    process_code: fields.processCode,
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
    const created = await request(`${api}/configuration/recipes`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    recipeId = created.id;
  }
  await loadData(recipeId);
  return state.recipe;
}

async function saveRecipe() {
  if (state.editorReadOnly) {
    notify("当前为配方详情，只能查看，不能保存修改。", "info", false);
    return;
  }
  try {
    const recipe = await ensureWorkingRecipe();
    await request(`${api}/configuration/recipes/${recipe.id}/save`, { method: "POST" });
    await loadData(recipe.id);
    notify("配方草稿已保存。发布前不会被 detect 接口匹配。", "success");
  } catch (error) {
    notify(error.message, "danger");
  }
}

async function publishRecipe() {
  if (state.editorReadOnly) {
    notify("当前为配方详情，只能查看，不能发布修改。", "info", false);
    return;
  }
  try {
    const recipe = await ensureWorkingRecipe();
    await request(`${api}/configuration/recipes/${recipe.id}/publish`, { method: "POST" });
    await loadData(recipe.id);
    notify("配方已发布，detect 接口现在可以按业务字段匹配该配方。");
  } catch (error) {
    notify(error.message, "danger");
  }
}

async function uploadBaseImage(file) {
  if (!file) return;
  if (state.editorReadOnly) {
    notify("当前为配方详情，只能查看，不能更换图片。", "info", false);
    return;
  }
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
    notify("图片上传成功。选择“绘制 ROI”或“绘制特征点”后，按住 Ctrl 并拖动鼠标开始配置。", "success", false);
  } catch (error) {
    notify(error.message, "danger");
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
  const stageOriginX = stageBounds.left + imageStage.clientLeft;
  const stageOriginY = stageBounds.top + imageStage.clientTop;
  const pointerInsideImage = clientX != null
    && clientY != null
    && clientX >= surfaceBounds.left
    && clientX <= surfaceBounds.right
    && clientY >= surfaceBounds.top
    && clientY <= surfaceBounds.bottom;
  const contentX = pointerInsideImage
    ? (clientX - surfaceBounds.left) / oldScale
    : view.displayWidth / 2;
  const contentY = pointerInsideImage
    ? (clientY - surfaceBounds.top) / oldScale
    : view.displayHeight / 2;
  const anchorX = pointerInsideImage
    ? clientX - stageOriginX
    : view.translateX + (view.displayWidth * oldScale) / 2;
  const anchorY = pointerInsideImage
    ? clientY - stageOriginY
    : view.translateY + (view.displayHeight * oldScale) / 2;
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
  const featureAnchor = state.recipe.feature_anchor;
  if (featureAnchor?.enabled) {
    const rect = featureAnchorRect(featureAnchor);
    context.fillStyle = "rgba(22,163,74,.12)";
    context.strokeStyle = "#15803d";
    context.lineWidth = 3;
    context.setLineDash([8, 5]);
    context.fillRect(rect.x, rect.y, rect.width, rect.height);
    context.strokeRect(rect.x, rect.y, rect.width, rect.height);
    context.setLineDash([]);
    context.fillStyle = "#166534";
    context.font = "700 13px Segoe UI, sans-serif";
    context.fillText("定位特征点", rect.x + 7, rect.y + 18);
  }
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
    const feature = state.drawMode === "FEATURE";
    context.strokeStyle = feature ? "#15803d" : "#ff6b35";
    context.fillStyle = feature ? "rgba(22,163,74,.12)" : "rgba(255,107,53,.12)";
    context.lineWidth = 3;
    context.setLineDash([8, 5]);
    context.fillRect(state.pendingRect.x, state.pendingRect.y, state.pendingRect.width, state.pendingRect.height);
    context.strokeRect(state.pendingRect.x, state.pendingRect.y, state.pendingRect.width, state.pendingRect.height);
    context.setLineDash([]);
  }
}

function featureAnchorRect(anchor) {
  return {
    x: anchor.x_ratio * canvas.width,
    y: anchor.y_ratio * canvas.height,
    width: anchor.width_ratio * canvas.width,
    height: anchor.height_ratio * canvas.height,
  };
}

function pointerPosition(event) {
  const canvasBounds = canvas.getBoundingClientRect();
  if (!canvasBounds.width || !canvasBounds.height) return { x: 0, y: 0 };
  return {
    x: Math.max(0, Math.min(canvas.width, (event.clientX - canvasBounds.left) * (canvas.width / canvasBounds.width))),
    y: Math.max(0, Math.min(canvas.height, (event.clientY - canvasBounds.top) * (canvas.height / canvasBounds.height))),
  };
}

function drawRectFromPoints(first, second) {
  const left = Math.max(0, Math.min(canvas.width, Math.min(first.x, second.x)));
  const top = Math.max(0, Math.min(canvas.height, Math.min(first.y, second.y)));
  const right = Math.max(left, Math.min(canvas.width, Math.max(first.x, second.x)));
  const bottom = Math.max(top, Math.min(canvas.height, Math.max(first.y, second.y)));
  return { x: left, y: top, width: right - left, height: bottom - top };
}

function hitRoi(point) {
  return [...(state.recipe?.rois || [])].reverse().find((roi) => {
    const { x, y, width, height } = roiRect(roi);
    return point.x >= x && point.x <= x + width && point.y >= y && point.y <= y + height;
  });
}

function hitResizeHandle(point) {
  const roi = selectedRoi();
  if (!roi) return null;
  const rect = state.workingRect || roiRect(roi);
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
  if ((!event.ctrlKey && event.button === 0) || event.button === 1) {
    beginImagePan(event);
    return;
  }
  if (state.editorReadOnly) return;
  if (event.button !== 0) return;
  const point = pointerPosition(event);
  const handle = hitResizeHandle(point);
  const existing = hitRoi(point);
  state.drawing = true;
  state.activePointerId = event.pointerId;
  state.startPoint = point;
  state.pointerRoi = existing;
  state.pendingRect = null;
  state.workingRect = null;
  if (state.drawMode === "FEATURE") {
    state.interactionMode = "feature-draw";
  } else if (handle && selectedRoi()) {
    state.interactionMode = `resize-${handle}`;
    state.originalRect = roiRect(selectedRoi());
  } else if (existing) {
    if (existing.id !== state.selectedRoiId) selectRoi(existing.id);
    state.interactionMode = "potential-move";
    state.originalRect = roiRect(existing);
  } else {
    state.interactionMode = "draw";
    state.selectedRoiId = null;
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
  if (!state.drawing || state.activePointerId !== event.pointerId) return;
  const point = pointerPosition(event);
  const dx = point.x - state.startPoint.x;
  const dy = point.y - state.startPoint.y;
  if (state.interactionMode === "potential-move" && (Math.abs(dx) > 3 || Math.abs(dy) > 3)) {
    state.interactionMode = "move";
  }
  if (["draw", "feature-draw"].includes(state.interactionMode)) {
    // Drawing uses the exact drag distance.  The minimum-size clamp is only
    // for moving/resizing an existing ROI; applying it here created a large
    // box even when the operator had not actually dragged one.
    state.pendingRect = drawRectFromPoints(state.startPoint, point);
  } else if (state.interactionMode === "move") {
    state.workingRect = clampRect({
      ...state.originalRect,
      x: state.originalRect.x + dx,
      y: state.originalRect.y + dy,
    });
  } else if (state.interactionMode?.includes("resize-")) {
    const handle = state.interactionMode.replace("resize-", "");
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

canvas.addEventListener("pointerup", async (event) => {
  if (state.imageView.panning) {
    endImagePan();
    if (canvas.hasPointerCapture(event.pointerId)) canvas.releasePointerCapture(event.pointerId);
    return;
  }
  if (!state.drawing || state.activePointerId !== event.pointerId) return;
  const endPoint = pointerPosition(event);
  if (["draw", "feature-draw"].includes(state.interactionMode)) {
    state.pendingRect = drawRectFromPoints(state.startPoint, endPoint);
  }
  state.drawing = false;
  state.activePointerId = null;
  if (canvas.hasPointerCapture(event.pointerId)) canvas.releasePointerCapture(event.pointerId);
  const mode = state.interactionMode;
  const selected = state.pointerRoi;
  state.pointerRoi = null;
  state.interactionMode = null;
  if (mode === "feature-draw" && state.pendingRect?.width > 8 && state.pendingRect?.height > 8) {
    await saveFeatureAnchorRect(state.pendingRect);
    return;
  }
  if (mode === "draw" && state.pendingRect?.width > 8 && state.pendingRect?.height > 8) {
    await createRoiFromRect(state.pendingRect);
    return;
  }
  if ((mode === "move" || mode?.startsWith("resize-")) && state.workingRect && selectedRoi()) {
    await persistRoiRect(selectedRoi(), state.workingRect);
    return;
  }
  state.pendingRect = null;
  state.workingRect = null;
  if (selected) {
    selectRoi(selected.id);
  } else {
    drawCanvas();
  }
});

canvas.addEventListener("pointercancel", (event) => {
  if (state.imageView.panning) endImagePan();
  if (canvas.hasPointerCapture(event.pointerId)) canvas.releasePointerCapture(event.pointerId);
  state.drawing = false;
  state.activePointerId = null;
  state.pendingRect = null;
  state.workingRect = null;
  state.interactionMode = null;
  drawCanvas();
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
  state.pendingRect = null;
  state.workingRect = null;
  renderConfiguredObjects();
  drawCanvas();
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
    state.pendingRect = null;
    await loadRecipe(state.recipe.id);
    await waitForBaseImage();
    selectRoi(created.id);
    await openObjectModal(created.id);
    notify("检测区域已创建。请选择一个已发布场景并填写对应校验值。", "info", false);
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
    const roiId = roi.id;
    state.workingRect = null;
    await loadRecipe(state.recipe.id);
    selectRoi(roiId);
    notify("检测区域坐标已更新。", "success", false);
  } catch (error) {
    state.workingRect = null;
    drawCanvas();
    notify(error.message, "danger", false);
  } finally {
    state.savingRoi = false;
  }
}

async function saveFeatureAnchorRect(rect) {
  state.savingRoi = true;
  try {
    await request(`${api}/configuration/recipes/${state.recipe.id}/feature-anchor`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        code: "FEATURE_ANCHOR",
        name: "图像定位特征点",
        x_ratio: rect.x / canvas.width,
        y_ratio: rect.y / canvas.height,
        width_ratio: rect.width / canvas.width,
        height_ratio: rect.height / canvas.height,
        padding: 0,
        enabled: true,
      }),
    });
    state.pendingRect = null;
    await loadRecipe(state.recipe.id);
    notify("图像定位特征点已保存。生产检测会先对齐实图，再按配方 ROI 计算裁剪区域。", "success", false);
  } catch (error) {
    state.pendingRect = null;
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
  fillRoiScenarioSelect(state.roiScenarioBindings.get(roi.id)?.scenario_version_id || null);
  refreshRoiScenarioBinding(roi);
  byId("roiAlignmentAnchor").checked = Boolean(roi.alignment_anchor);
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
      const profile = item.rule_json?.color_profile;
      return {
        type: "COLOR",
        scene: inferItemScene(item, roi),
        value: String(item.expected_json.color || "").toLowerCase(),
        colorAnalysis: profile ? { profile } : null,
      };
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
  const scenarioVersionId = Number(byId("roiScenarioVersion").value) || null;
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
        alignment_anchor: byId("roiAlignmentAnchor").checked,
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
        const colorProfile = rule.colorAnalysis?.profile;
        const usesCurrentColorProfile = String(colorProfile?.color || "").toUpperCase() === value.toUpperCase();
        const baselineRatio = usesCurrentColorProfile
          ? Number(colorProfile?.baseline_ratio)
          : Number.NaN;
        payload = {
          inspection_type: "COLOR",
          capability: "COLOR_RATIO",
          reference_group_id: null,
          expected_json: { color: value.toUpperCase() },
          rule_json: {
            min_ratio: Number.isFinite(baselineRatio) ? Math.max(0.05, baselineRatio * 0.55) : 0.15,
            max_ratio: 1,
            color_profile: usesCurrentColorProfile ? colorProfile : null,
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
    if (scenarioVersionId) {
      const binding = await request(`${api}/scenarios/rois/${roi.id}/binding`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ scenario_version_id: scenarioVersionId }),
      });
      state.roiScenarioBindings.set(roi.id, binding);
    } else {
      try {
        await request(`${api}/scenarios/rois/${roi.id}/binding`, { method: "DELETE" });
      } catch (error) {
        if (!String(error.message).includes("未关联")) throw error;
      }
      state.roiScenarioBindings.delete(roi.id);
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
  if (details.scoring_mode === "ROBUST_TOP_K") {
    if (details.similarity != null) values["最终稳健分"] = Number(details.similarity).toFixed(4);
    if (details.top1_similarity != null) values["最高相似度"] = Number(details.top1_similarity).toFixed(4);
    if (details.top_k_mean != null) values[`Top-${Number(details.selected_count || 1)} 平均`] = Number(details.top_k_mean).toFixed(4);
    if (details.top_k_std != null) values["参考分数离散度"] = Number(details.top_k_std).toFixed(4);
    if (details.passing_reference_count != null) values["达到阈值参考图"] = `${Number(details.passing_reference_count)} / ${Number(details.selected_count || 1)}`;
  } else if (details.similarity != null) {
    values["相似度"] = Number(details.similarity).toFixed(4);
  }
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

// The legacy rule editor is retained below only for backward-compatible
// records. New recipe ROIs are exclusively driven by published scenes.
function renderConfiguredObjects() {
  const rois = state.recipe?.rois || [];
  const anchor = state.recipe?.feature_anchor;
  const readOnly = state.editorReadOnly;
  const anchorStatus = byId("featureAnchorStatus");
  if (anchorStatus) {
    anchorStatus.innerHTML = anchor?.enabled
      ? `<div class="feature-anchor-card"><div><small>图像定位</small><strong>${escapeHtml(anchor.name || "定位特征点")}</strong><span>生产检测先以此区域对齐实图</span></div>${readOnly ? "" : '<button class="btn btn-sm btn-outline-danger" type="button" id="deleteFeatureAnchor">删除</button>'}</div>`
      : '<div class="feature-anchor-card empty"><div><small>图像定位</small><strong>尚未配置特征点</strong><span>可选择固定螺钉、孔位或 PCB 特征后，切换到“绘制特征点”框选。</span></div></div>';
  }
  byId("configuredObjectList").innerHTML = rois.length
    ? rois.map((roi, index) => {
      const binding = state.roiScenarioBindings.get(roi.id);
      const mapping = binding?.input_mapping_json || {};
      const fieldText = Object.entries(mapping).map(([key, value]) => `${key} = ${value}`).join("；");
      return `
        <article class="configured-object-card ${roi.id === state.selectedRoiId ? "selected" : ""}" data-detail-roi="${roi.id}">
          <div class="configured-object-heading">
            <span>${String(index + 1).padStart(2, "0")}</span>
            <div class="configured-object-identity"><small>${escapeHtml(binding?.scenario_code || "未关联场景")}</small><strong>${escapeHtml(roi.code)}</strong></div>
            <div class="configured-object-quick-actions">${readOnly
              ? '<button class="btn btn-sm btn-outline-secondary view-object" type="button">查看</button>'
              : '<button class="btn btn-sm btn-outline-primary edit-object" type="button">编辑</button><button class="btn btn-sm btn-outline-danger delete-object" type="button">删除</button>'}</div>
          </div>
          <div class="configured-object-summary">
            <span>${binding ? escapeHtml(binding.scenario_name || binding.scenario_code) : "请关联已发布场景"}</span>
            <span>${fieldText ? escapeHtml(fieldText) : "无需额外校验值"}</span>
          </div>
        </article>`;
    }).join("")
    : '<div class="no-object-selected compact"><span>⌖</span><strong>还没有检测区域</strong><p>选择“绘制 ROI”，按住 Ctrl 后在左侧图片拖动画框。</p></div>';
}

async function openObjectModal(roiId) {
  selectRoi(roiId);
  await populateObjectEditor();
  applyObjectModalAccessMode();
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

function applyObjectModalAccessMode() {
  const modal = byId("objectConfigModal");
  const readOnly = state.editorReadOnly;
  modal.classList.toggle("object-config-readonly", readOnly);
  byId("objectConfigReadOnlyHint").hidden = !readOnly;
  byId("saveRoiRules").hidden = readOnly;
  byId("testRoiRules").hidden = readOnly;
  modal.querySelectorAll("select, input:not([readonly]), textarea").forEach((field) => {
    field.disabled = readOnly;
  });
  byId("addRoiScenarioInput").disabled = readOnly || !sceneInputsForVersion(byId("roiScenarioVersion").value).length;
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

async function populateObjectEditor() {
  const roi = selectedRoi();
  if (!roi) return;
  resetInlineRoiTest();
  byId("roiRuleStatus").textContent = "";
  byId("roiRuleStatus").className = "roi-rule-status";
  byId("selectedObjectTitle").textContent = roi.code;
  await refreshRoiScenarioBinding(roi);
  const binding = state.roiScenarioBindings.get(roi.id) || {};
  fillRoiScenarioSelect(binding.scenario_version_id || null);
  renderRoiScenarioInputRows(
    binding.scenario_version_id,
    binding.input_mapping_json || {},
  );
  byId("roiPointX").value = `${Math.round(roi.x_ratio * baseImage.naturalWidth)} px`;
  byId("roiPointY").value = `${Math.round(roi.y_ratio * baseImage.naturalHeight)} px`;
  byId("roiPointWidth").value = `${Math.round(roi.width_ratio * baseImage.naturalWidth)} px`;
  byId("roiPointHeight").value = `${Math.round(roi.height_ratio * baseImage.naturalHeight)} px`;
  updateRoiReferencePreview(roi);
}

async function saveRoiRules({ quiet = false } = {}) {
  if (state.editorReadOnly) {
    if (!quiet) notify("当前为配方详情，只能查看 ROI 与场景关联。", "info", false);
    return false;
  }
  const roi = selectedRoi();
  if (!roi) return false;
  const scenarioVersionId = Number(byId("roiScenarioVersion").value) || null;
  if (!scenarioVersionId) {
    notify("请先选择一个已发布检测场景。", "warning", false);
    return false;
  }
  let inputMappingJson;
  try {
    inputMappingJson = collectRoiScenarioInputMapping();
  } catch (error) {
    notify(error.message, "warning", false);
    return false;
  }
  const saveButton = byId("saveRoiRules");
  const testButton = byId("testRoiRules");
  const status = byId("roiRuleStatus");
  saveButton.disabled = true;
  testButton.disabled = true;
  saveButton.textContent = "正在保存…";
  status.textContent = "正在保存 ROI 与场景关联…";
  status.className = "roi-rule-status working";
  try {
    const rect = roiRect(roi);
    await request(`${api}/configuration/rois/${roi.id}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        code: roi.code,
        name: roi.name,
        object_type: roi.object_type || "OBJECT",
        padding: roi.padding || 0,
        sort_order: roi.sort_order || 0,
        x_ratio: rect.x / canvas.width,
        y_ratio: rect.y / canvas.height,
        width_ratio: rect.width / canvas.width,
        height_ratio: rect.height / canvas.height,
      }),
    });
    const binding = await request(`${api}/scenarios/rois/${roi.id}/binding`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ scenario_version_id: scenarioVersionId, input_mapping_json: inputMappingJson }),
    });
    state.roiScenarioBindings.set(roi.id, binding);
    const roiId = roi.id;
    await loadRecipe(state.recipe.id);
    selectRoi(roiId);
    status.textContent = "保存成功";
    status.className = "roi-rule-status success";
    if (!quiet) notify("ROI 已关联已发布场景。保存配方后仍需点击发布，detect 接口才会使用。", "success", false);
    return true;
  } catch (error) {
    status.textContent = `保存失败：${error.message}`;
    status.className = "roi-rule-status error";
    if (!quiet) notify(error.message, "danger", false);
    return false;
  } finally {
    saveButton.disabled = false;
    testButton.disabled = false;
    saveButton.textContent = "保存场景关联";
  }
}

async function testCurrentRoiRules() {
  if (state.editorReadOnly) {
    notify("详情页不能修改或测试当前 ROI，请从配方库使用“测试”操作。", "info", false);
    return;
  }
  const saved = await saveRoiRules({ quiet: true });
  if (!saved) return;
  const roi = selectedRoi();
  if (!roi || !state.recipe?.base_image_url) return;
  const button = byId("testRoiRules");
  const status = byId("roiRuleStatus");
  button.disabled = true;
  button.textContent = "正在测试…";
  showInlineRoiTestLoading(roi);
  try {
    const source = await fetch(state.recipe.base_image_url);
    if (!source.ok) throw new Error("无法读取当前配方基准图片。");
    const blob = await source.blob();
    const data = new FormData();
    data.append("recipe_id", state.recipe.id);
    data.append("roi_id", roi.id);
    data.append("file", new File([blob], `${roi.code}-test.jpg`, { type: blob.type || "image/jpeg" }));
    const result = await request(`${api}/inspection/test`, { method: "POST", body: data });
    renderInlineRoiTestResult(result, roi);
    status.textContent = `测试完成：${result.result}`;
    status.className = `roi-rule-status ${result.result === "OK" ? "success" : "error"}`;
  } catch (error) {
    renderInlineRoiTestError(error, roi);
    status.textContent = `测试失败：${error.message}`;
    status.className = "roi-rule-status error";
  } finally {
    button.disabled = false;
    button.textContent = "测试当前 ROI";
  }
}

function resetInlineRoiTest() {
  const panel = byId("roiInlineTestPanel");
  if (!panel) return;
  panel.hidden = true;
  byId("roiInlineTestTitle").textContent = "等待测试";
  byId("roiInlineTestSummary").textContent = "测试会使用当前 ROI 已关联的发布场景，不会发布工艺配方。";
  byId("roiInlineTestBadge").className = "result-badge waiting";
  byId("roiInlineTestBadge").textContent = "WAITING";
  byId("roiInlineTestOverview").innerHTML = "";
  byId("roiInlineRuleResults").innerHTML = "";
  byId("roiInlineReferenceImage").removeAttribute("src");
  byId("roiInlineTestImage").removeAttribute("src");
}

function showInlineRoiTestLoading(roi) {
  const panel = byId("roiInlineTestPanel");
  panel.hidden = false;
  byId("roiInlineTestTitle").textContent = `${roi.code} 正在测试`;
  byId("roiInlineTestSummary").textContent = "正在裁剪当前 ROI 并执行关联场景…";
  byId("roiInlineTestBadge").className = "result-badge waiting";
  byId("roiInlineTestBadge").textContent = "RUNNING";
  byId("roiInlineTestOverview").innerHTML = `<div><small>当前区域</small><strong>${escapeHtml(roi.code)}</strong></div>`;
  byId("roiInlineRuleResults").innerHTML = '<div class="roi-inline-loading"><span></span>正在执行场景…</div>';
  const referenceUrl = byId("roiReferenceImage")?.getAttribute("src") || "";
  byId("roiInlineReferenceImage").src = referenceUrl;
  byId("roiInlineReferenceImage").hidden = !referenceUrl;
  byId("roiInlineReferenceEmpty").hidden = Boolean(referenceUrl);
}

function renderInlineRoiTestResult(result, roi) {
  const item = result.image_results?.[0]?.inspection_items?.[0];
  const status = item?.status || result.result || "ERROR";
  const output = item?.actual || {};
  byId("roiInlineTestPanel").hidden = false;
  byId("roiInlineTestTitle").textContent = `${roi.code} 测试完成`;
  byId("roiInlineTestSummary").textContent = "以下显示关联场景的实际输出；需要修改校验值时请返回上方字段调整。";
  byId("roiInlineTestBadge").className = `result-badge ${String(status).toLowerCase()}`;
  byId("roiInlineTestBadge").textContent = status;
  byId("roiInlineTestOverview").innerHTML = `
    <div><small>最终结果</small><strong>${escapeHtml(status)}</strong></div>
    <div><small>场景</small><strong>${escapeHtml(item?.item_name || "-")}</strong></div>
    <div><small>耗时</small><strong>${Number(item?.elapsed_ms || result.elapsed_ms || 0).toFixed(2)} ms</strong></div>`;
  const roiImageUrl = item?.roi_image_url || result.image_results?.[0]?.roi_image_url || "";
  byId("roiInlineTestImage").src = roiImageUrl ? `${roiImageUrl}?v=${Date.now()}` : "";
  byId("roiInlineRuleResults").innerHTML = `
    <article class="roi-inline-rule-card ${String(status).toLowerCase()}">
      <header><div><span>关联场景</span><strong>${escapeHtml(item?.item_name || "场景输出")}</strong></div><span class="result-badge ${String(status).toLowerCase()}">${escapeHtml(status)}</span></header>
      <div class="roi-inline-rule-condition"><span>场景字段 / 校验值</span><strong>${escapeHtml(Object.entries(state.roiScenarioBindings.get(roi.id)?.input_mapping_json || {}).map(([key, value]) => `${key} = ${value}`).join("；") || "无")}</strong></div>
      <section class="roi-inline-model-card primary"><header><span>场景执行输出</span><strong>${escapeHtml(item?.capability || "-")}</strong></header><details open><summary>查看结构化结果</summary><pre>${escapeHtml(JSON.stringify(output, null, 2))}</pre></details></section>
    </article>`;
}

function renderInlineRoiTestError(error, roi) {
  byId("roiInlineTestPanel").hidden = false;
  byId("roiInlineTestTitle").textContent = `${roi.code} 测试失败`;
  byId("roiInlineTestSummary").textContent = "可检查场景是否已发布、模型服务是否可用，以及场景字段是否完整。";
  byId("roiInlineTestBadge").className = "result-badge error";
  byId("roiInlineTestBadge").textContent = "ERROR";
  byId("roiInlineTestOverview").innerHTML = `<div><small>错误信息</small><strong>${escapeHtml(error.message)}</strong></div>`;
  byId("roiInlineRuleResults").innerHTML = `<div class="roi-inline-test-error"><strong>测试未完成</strong><p>${escapeHtml(error.message)}</p></div>`;
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

function recipeFamilies() {
  const groups = new Map();
  state.recipes.forEach((recipe) => {
    const key = recipe.recipe_family_code || recipe.code || `recipe-${recipe.id}`;
    const rows = groups.get(key) || [];
    rows.push(recipe);
    groups.set(key, rows);
  });
  return [...groups.values()].map((versions) => {
    const ordered = [...versions].sort((left, right) => Number(right.id) - Number(left.id));
    const draft = ordered.find((item) => ["DRAFT", "SAVED"].includes(item.status));
    const published = ordered.find((item) => item.status === "PUBLISHED");
    return {
      versions: ordered,
      draft,
      published,
      display: draft || published || ordered[0],
    };
  });
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
  const filtered = recipeFamilies().filter((family) => {
    const recipe = family.display;
    const matchesQuery = !query || family.versions.some((version) => {
      const detail = state.details.get(version.id);
      return JSON.stringify({ ...version, ...detail }).toLowerCase().includes(query);
    });
    const matchesStatus = !filters.status || family.versions.some((version) => version.status === filters.status);
    const matchesFields = Object.entries(filters)
      .filter(([field]) => field !== "status")
      .every(([field, value]) => !value || recipe[field] === value);
    return matchesQuery && matchesStatus && matchesFields;
  });
  filtered.sort((left, right) => {
    if (sortMode === "NAME_ASC") return String(left.display.name).localeCompare(String(right.display.name), "zh-CN");
    if (sortMode === "MATERIAL_ASC") return String(left.display.material_code).localeCompare(String(right.display.material_code), "zh-CN");
    return Number(right.display.id) - Number(left.display.id);
  });
  const totalPages = Math.max(1, Math.ceil(filtered.length / state.libraryPageSize));
  state.libraryPage = Math.min(state.libraryPage, totalPages);
  const start = (state.libraryPage - 1) * state.libraryPageSize;
  const visibleRecipes = filtered.slice(start, start + state.libraryPageSize);
  byId("libraryResultsMeta").textContent = filtered.length
    ? `共 ${filtered.length} 个配方，当前显示第 ${start + 1}–${Math.min(start + state.libraryPageSize, filtered.length)} 个`
    : "没有符合当前条件的配方";
  byId("configurationLibrary").innerHTML = filtered.length
    ? visibleRecipes.map((family) => {
      const recipe = family.display;
      const productionStatus = family.published
        ? `<span class="status-pill published">${escapeHtml(family.published.display_version || "已发布")}</span>`
        : '<span class="status-pill draft">未发布</span>';
      const draftStatus = family.draft
        ? `<small>${escapeHtml(family.draft.display_version || "草稿编辑中")}</small>`
        : '<small>无待发布草稿</small>';
      return `
        <tr data-recipe-id="${recipe.id}" data-production-recipe-id="${family.published?.id || ""}" data-draft-recipe-id="${family.draft?.id || ""}">
          <td>${escapeHtml(recipe.project_name || "-")}</td>
          <td><strong>${escapeHtml(recipe.name)}</strong><small>${escapeHtml(recipe.code)} · ${family.versions.length} 个版本</small></td>
          <td>${escapeHtml(recipe.line_code || "-")}</td>
          <td>${escapeHtml(recipe.material_code || "-")}</td>
          <td>${escapeHtml(recipe.process_code || "-")}</td>
          <td>${escapeHtml(recipe.camera_code || "-")} / ${recipe.capture_index}</td>
          <td>${recipe.roi_count}</td>
          <td><div class="recipe-version-status">${productionStatus}${draftStatus}</div></td>
          <td class="recipe-table-actions"><button class="btn btn-sm btn-outline-secondary detail-recipe" type="button">详情</button><button class="btn btn-sm btn-outline-primary edit-recipe" type="button" title="编辑会打开同一配方的唯一草稿，不会影响生产版本">编辑</button><button class="btn btn-sm btn-outline-secondary history-recipe" type="button">历史 / 回滚</button><button class="btn btn-sm btn-outline-secondary copy-recipe" type="button">复制</button><button class="btn btn-sm btn-primary test-recipe" type="button">测试</button><button class="btn btn-sm btn-outline-danger delete-recipe" type="button">删除草稿</button></td>
        </tr>`;
    }).join("")
    : '<tr><td colspan="9"><div class="library-no-results">没有找到匹配的工艺配方，请清除筛选或创建新配方。</div></td></tr>';
  renderLibraryPagination(totalPages);
}

function formatRecipeHistoryTime(value) {
  if (!value) return "未记录时间";
  const utcValue = /(?:Z|[+-]\d{2}:\d{2})$/.test(value) ? value : `${value}Z`;
  const parsed = new Date(utcValue);
  return Number.isNaN(parsed.getTime())
    ? String(value)
    : parsed.toLocaleString("zh-CN", { hour12: false });
}

function isEditableRecipeVersion(version) {
  return ["DRAFT", "SAVED"].includes(String(version?.status || "").toUpperCase());
}

function openRecipeHistory(history, sourceRecipeId) {
  const versions = Array.isArray(history?.versions) ? history.versions : [];
  const editableDraft = versions.find(isEditableRecipeVersion);
  state.recipeHistory = { sourceRecipeId: Number(sourceRecipeId), versions };
  const latest = versions[0] || {};
  byId("recipeHistoryTitle").textContent = latest.name
    ? `历史版本与回滚 · ${latest.name}`
    : "历史版本与回滚";
  byId("recipeHistoryIntro").textContent = editableDraft
    ? `当前已有 ${editableDraft.display_version || "未发布草稿"}，请先继续编辑、发布或删除该草稿后再回滚其他版本。`
    : "选择一个历史版本后，系统会复制出新的待发布草稿；生产中正在使用的已发布版本不会被覆盖。";
  byId("recipeHistoryList").innerHTML = versions.length
    ? versions.map((version) => {
      const editable = isEditableRecipeVersion(version);
      const current = Number(version.id) === Number(sourceRecipeId);
      const canRollback = !editable && !editableDraft;
      const statusLabel = {
        PUBLISHED: "已发布（生产使用）",
        ARCHIVED: "历史版本",
        SAVED: "已保存草稿",
        DRAFT: "草稿",
      }[String(version.status || "").toUpperCase()] || version.status || "未知状态";
      const action = editable
        ? `<button class="btn btn-sm btn-outline-primary" type="button" data-recipe-history-open-draft="${version.id}">继续编辑</button>`
        : `<button class="btn btn-sm btn-primary" type="button" data-recipe-history-rollback="${version.id}" ${canRollback ? "" : "disabled"}>回滚为草稿</button>`;
      const disabledHint = !canRollback && !editable ? "<small>需先处理当前未发布草稿</small>" : "";
      return `<article class="recipe-history-item ${current ? "current" : ""} ${editable ? "draft" : ""}">
        <div>
          <strong>${escapeHtml(version.display_version || version.version || `版本 #${version.id}`)}</strong>
          <small>${escapeHtml(version.code || "-")} · 创建于 ${escapeHtml(formatRecipeHistoryTime(version.created_at))}</small>
          <div class="recipe-history-item-meta"><span>${escapeHtml(statusLabel)}</span>${current ? "<span>当前生产参考版本</span>" : ""}${version.source_recipe_id ? `<span>来源 #${version.source_recipe_id}</span>` : ""}</div>
        </div>
        <div>${action}${disabledHint}</div>
      </article>`;
    }).join("")
    : '<div class="recipe-history-empty">当前配方还没有可用的历史版本。</div>';
  window.bootstrap.Modal.getOrCreateInstance(byId("recipeHistoryModal")).show();
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
  if (details.scoring_mode === "ROBUST_TOP_K" && details.similarity != null) {
    return `稳健分 ${Number(details.similarity).toFixed(3)} / Top1 ${Number(details.top1_similarity).toFixed(3)}`;
  }
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

function recordOperation(record) {
  return record.operation || record.request_payload?.operation || record.request_payload?.process_code || "-";
}

function recordImageCard(title, imageUrl, emptyText) {
  return imageUrl
    ? `<figure class="record-detail-image"><figcaption>${escapeHtml(title)}</figcaption><img src="${escapeHtml(imageUrl)}?v=${Date.now()}" alt="${escapeHtml(title)}"></figure>`
    : `<div class="record-detail-image empty"><strong>${escapeHtml(title)}</strong><span>${escapeHtml(emptyText)}</span></div>`;
}

function renderDetectionRecordDetail(detail) {
  const recipeCards = (detail.recipes || []).map((recipe) => `
    <section class="record-recipe-detail">
      <header>
        <div><small>工艺配方</small><h3>${escapeHtml(recipe.recipe_code)} · ${escapeHtml(recipe.recipe_name)}</h3></div>
        <span class="result-badge ${(recipe.result || "error").toLowerCase()}">${escapeHtml(recipe.result || "ERROR")}</span>
      </header>
      <div class="record-recipe-image-row">
        ${recordImageCard("配方标准图", recipe.standard_image_url, "该配方尚未保存标准图")}
      </div>
      ${(recipe.images || []).map((image) => `
        <article class="record-image-detail">
          <header><strong>${escapeHtml(image.source_image_path || "实测图片")}</strong><span class="result-badge ${(image.status || "error").toLowerCase()}">${escapeHtml(image.status || "ERROR")}</span></header>
          <div class="record-recipe-image-row">
            ${recordImageCard("本地实测图", image.actual_image_url, "本地缓存图不存在")}
            ${recordImageCard("处理结果图", image.result_image_url, "结果图不存在")}
          </div>
          <div class="record-roi-detail-grid">
            ${(image.rois || []).map((roi) => `
              <article class="record-roi-detail ${String(roi.status || "ERROR").toLowerCase()}">
                <header><div><strong>${escapeHtml(roi.roi_code || "ROI")}</strong><small>${escapeHtml(roi.roi_name || "-")}</small></div><b>${escapeHtml(roi.status || "ERROR")}</b></header>
                <div class="record-roi-image-row">
                  ${recordImageCard("标准 ROI", roi.standard_roi_image_url, "未配置基准图")}
                  ${recordImageCard("实测 ROI", roi.actual_roi_image_url, "处理图不存在")}
                </div>
                <dl>
                  <div><dt>关联场景</dt><dd>${escapeHtml(roi.scene_name || "-")}</dd></div>
                  <div><dt>模型分数</dt><dd>${roi.score == null ? "-" : escapeHtml(Number(roi.score).toFixed(4))}</dd></div>
                  <div><dt>处理说明</dt><dd>${escapeHtml(roi.message || "-")}</dd></div>
                </dl>
                <details><summary>查看模型处理结果</summary><pre>${escapeHtml(JSON.stringify(roi.processing || {}, null, 2))}</pre></details>
              </article>`).join("") || '<div class="records-empty">该图片没有 ROI 处理明细</div>'}
          </div>
        </article>`).join("") || '<div class="records-empty">该配方没有图片处理结果</div>'}
    </section>`).join("") || '<div class="records-empty">本次调用未产生可追溯的配方执行结果</div>';
  byId("detectionRecordDetailTitle").textContent = `${detail.sn || "-"} · ${recordOperation(detail)}`;
  byId("detectionRecordDetailContent").innerHTML = `
    <div class="record-detail-meta"><span>调用方：<code>${escapeHtml(detail.caller_ip || "-")}</code></span><span>调用时间：${escapeHtml(formatDetectionTime(detail.called_at))}</span></div>
    ${recipeCards}`;
}

async function openDetectionRecordDetail(recordId) {
  const content = byId("detectionRecordDetailContent");
  content.innerHTML = '<div class="records-empty">正在加载配方、标准图和 ROI 对比…</div>';
  const modalElement = byId("detectionRecordDetailModal");
  window.bootstrap?.Modal.getOrCreateInstance(modalElement).show();
  try {
    const detail = await request(`${api}/inspection/call-records/${recordId}`);
    renderDetectionRecordDetail(detail);
  } catch (error) {
    content.innerHTML = `<div class="records-empty error">${escapeHtml(error.message)}</div>`;
  }
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
            <td>${escapeHtml(recordOperation(record))}</td>
            <td><span class="call-status ${record.response_code === 0 ? "success" : "failed"}">${record.response_code} · ${escapeHtml(record.call_status)}</span></td>
            <td><span class="result-badge ${(response.result || "error").toLowerCase()}">${escapeHtml(response.result || "ERROR")}</span></td>
            <td>${renderRecordModelResults(response)}</td>
            <td>${record.elapsed_ms == null ? "-" : `${record.elapsed_ms} ms`}</td>
            <td>
              <button class="btn btn-sm btn-outline-primary record-detail-button" type="button" data-record-detail="${record.id}">详情</button>
              <details class="call-payload-details">
                <summary>查看参数</summary>
                <div><strong>调用参数</strong><pre>${escapeHtml(requestPayload)}</pre></div>
                <div><strong>返回参数</strong><pre>${escapeHtml(responsePayload)}</pre></div>
              </details>
            </td>
          </tr>`;
      }).join("")
    : '<tr><td colspan="9" class="records-empty">暂无第三方检测调用记录</td></tr>';
}

async function loadDetectionRecords() {
  byId("detectionRecordsBody").innerHTML =
    '<tr><td colspan="9" class="records-empty">正在加载检测记录…</td></tr>';
  try {
    const sn = byId("detectionRecordSnFilter").value.trim();
    const query = new URLSearchParams({ limit: "200" });
    if (sn) query.set("sn", sn);
    state.detectionRecords = await request(`${api}/inspection/call-records?${query.toString()}`);
    renderDetectionRecords();
  } catch (error) {
    byId("detectionRecordsBody").innerHTML =
      `<tr><td colspan="9" class="records-empty error">${escapeHtml(error.message)}</td></tr>`;
  }
}

function formatRate(value) {
  return `${(Number(value || 0) * 100).toFixed(1)}%`;
}

function localDateInputValue(value) {
  const year = value.getFullYear();
  const month = String(value.getMonth() + 1).padStart(2, "0");
  const day = String(value.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

function initializeReportDateRange() {
  const end = new Date();
  const start = new Date(end.getFullYear(), end.getMonth() - 11, 1);
  byId("reportStartDate").value = localDateInputValue(start);
  byId("reportEndDate").value = localDateInputValue(end);
}

function reportTrendChart(title, periods, key, color, formatter) {
  const values = periods.map((row) => Number(row[key] || 0));
  const maximum = Math.max(1, ...values);
  const width = 640;
  const height = 230;
  const left = 42;
  const right = 14;
  const top = 18;
  const bottom = 36;
  const plotWidth = width - left - right;
  const plotHeight = height - top - bottom;
  const denominator = Math.max(1, values.length - 1);
  const points = values.map((value, index) => {
    const x = left + (plotWidth * index) / denominator;
    const y = top + plotHeight - (value / maximum) * plotHeight;
    return `${x.toFixed(2)},${y.toFixed(2)}`;
  }).join(" ");
  const last = values.at(-1) || 0;
  const firstDate = periods[0]?.date || "-";
  const lastDate = periods.at(-1)?.date || "-";
  return `
    <section class="report-chart-card">
      <header><div><strong>${escapeHtml(title)}</strong><small>${escapeHtml(firstDate)} 至 ${escapeHtml(lastDate)}</small></div><b>${escapeHtml(formatter(last))}</b></header>
      <svg viewBox="0 0 ${width} ${height}" role="img" aria-label="${escapeHtml(title)}趋势图">
        <line x1="${left}" y1="${top}" x2="${left}" y2="${height - bottom}" class="report-axis"></line>
        <line x1="${left}" y1="${height - bottom}" x2="${width - right}" y2="${height - bottom}" class="report-axis"></line>
        <line x1="${left}" y1="${top + plotHeight / 2}" x2="${width - right}" y2="${top + plotHeight / 2}" class="report-grid-line"></line>
        <polyline points="${points}" fill="none" stroke="${color}" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"></polyline>
        <text x="${left}" y="${height - 12}" class="report-axis-label">${escapeHtml(firstDate.slice(5))}</text>
        <text x="${width - right}" y="${height - 12}" text-anchor="end" class="report-axis-label">${escapeHtml(lastDate.slice(5))}</text>
        <text x="${left - 7}" y="${top + 5}" text-anchor="end" class="report-axis-label">${escapeHtml(formatter(maximum))}</text>
        <text x="${left - 7}" y="${height - bottom + 4}" text-anchor="end" class="report-axis-label">0</text>
      </svg>
    </section>`;
}

function reportSceneValue(row, key) {
  const raw = row.raw || {};
  const values = {
    name: row.key || "",
    total: Number(raw.total || 0),
    primary_pass_rate: Number(row.primary_pass_rate ?? raw.ok_rate ?? 0),
    review_coverage: Number(row.review_coverage || 0),
    review_accuracy: Number(row.review_accuracy ?? row.review_agreement_rate ?? -1),
    human_reviewed_count: Number(row.human_reviewed_count || 0),
    manual_accuracy: Number(row.manual_accuracy ?? row.confirmed_accuracy ?? -1),
  };
  return values[key] ?? "";
}

function sceneSortIcon(key) {
  if (state.reportSceneSort.key !== key) return "↕";
  return state.reportSceneSort.direction === "asc" ? "↑" : "↓";
}

function sceneReportDimensionCard(rows) {
  const search = state.reportSceneSearch.trim().toLocaleLowerCase();
  const filteredRows = rows
    .filter((row) => !search || String(row.key || "").toLocaleLowerCase().includes(search))
    .sort((left, right) => {
      const key = state.reportSceneSort.key;
      const direction = state.reportSceneSort.direction === "asc" ? 1 : -1;
      const leftValue = reportSceneValue(left, key);
      const rightValue = reportSceneValue(right, key);
      if (typeof leftValue === "string") return direction * leftValue.localeCompare(String(rightValue), "zh-CN");
      return direction * (Number(leftValue) - Number(rightValue));
    });
  const header = (label, key, extra = "") => `<th><button class="report-sort-button" type="button" data-scene-sort="${key}">${escapeHtml(label)} <span>${sceneSortIcon(key)}</span></button>${extra}</th>`;
  const body = filteredRows.length
    ? filteredRows.map((row) => {
      const raw = row.raw || {};
      return `
        <tr>
          <td>${escapeHtml(row.key)}</td>
          <td>${raw.total || 0}</td>
          <td>${formatRate(row.primary_pass_rate ?? raw.ok_rate)}</td>
          <td>${formatRate(row.review_coverage)}</td>
          <td>${row.reviewed_count ? formatRate(row.review_accuracy ?? row.review_agreement_rate) : "待复核"}</td>
          <td>${row.human_reviewed_count || 0}</td>
          <td>${row.confirmed_count ? formatRate(row.manual_accuracy ?? row.confirmed_accuracy) : "待人工确认"}</td>
        </tr>`;
    }).join("")
    : '<tr><td colspan="7" class="records-empty">没有匹配的场景生产记录</td></tr>';
  return `
    <section class="report-dimension-card report-scene-card">
      <div class="report-scene-heading"><h3>场景统计</h3><label class="report-scene-search"><span>⌕</span><input id="sceneReportSearch" value="${escapeHtml(state.reportSceneSearch)}" placeholder="筛选场景名称"></label></div>
      <div class="report-table-wrap"><table>
        <thead><tr>${header("场景名称", "name")}${header("调用次数", "total")}${header("原始通过率", "primary_pass_rate")}${header("复核覆盖率", "review_coverage")}${header("复核参考准确率", "review_accuracy")}${header("人工确认", "human_reviewed_count")}${header("人工确认准确率", "manual_accuracy")}</tr></thead>
        <tbody>${body}</tbody>
      </table></div>
    </section>`;
}

function renderInspectionReports() {
  const report = state.inspectionReports;
  if (!report) return;
  const overall = report.overall || {};
  byId("inspectionReportNote").textContent = report.accuracy_note || "";
  byId("inspectionReportSummary").innerHTML = `
    <span><small>场景执行</small><strong>${overall.total || 0}</strong></span>
    <span><small>原始通过率</small><strong>${formatRate(overall.ok_rate)}</strong></span>
    <span><small>复核覆盖率</small><strong>${formatRate(overall.review_coverage)}</strong></span>
    <span><small>复核参考准确率</small><strong>${overall.reviewed_count ? formatRate(overall.review_accuracy ?? overall.review_agreement_rate) : "待复核"}</strong></span>
    <span><small>人工确认准确率</small><strong>${overall.confirmed_count ? formatRate(overall.confirmed_accuracy) : "待人工确认"}</strong></span>
    `;
  const dimensions = report.dimensions || {};
  byId("inspectionReportCharts").innerHTML = [
    reportTrendChart("每月场景执行量", report.monthly || [], "total", "#2a76d2", (value) => `${value} 次`),
    reportTrendChart("每月复核参考准确率", report.monthly || [], "review_accuracy", "#2a76d2", formatRate),
  ].join("");
  byId("inspectionReportDimensions").innerHTML = [
    sceneReportDimensionCard(dimensions.scene || []),
  ].join("");
}

async function loadInspectionReports() {
  const button = byId("refreshInspectionReports");
  button.disabled = true;
  button.textContent = "正在统计…";
  try {
    const startDate = byId("reportStartDate").value;
    const endDate = byId("reportEndDate").value;
    if (!startDate || !endDate) throw new Error("请选择统计开始日期和结束日期");
    const query = new URLSearchParams({ start_date: startDate, end_date: endDate });
    state.inspectionReports = await request(`${api}/inspection/reports?${query.toString()}`);
    renderInspectionReports();
  } catch (error) {
    byId("inspectionReportNote").textContent = `统计报表加载失败：${error.message}`;
  } finally {
    button.disabled = false;
    button.textContent = "刷新报表";
  }
}

function switchView(viewId) {
  document.querySelectorAll(".workspace-switch").forEach((button) => {
    button.classList.toggle("active", button.dataset.view === viewId);
  });
  document.querySelectorAll(".workspace-view").forEach((view) => {
    view.classList.toggle("active", view.id === viewId);
  });
}

async function openWorkspaceViewFromQuery() {
  const workspace = document.querySelector(".workspace-shell");
  const requestedView = new URLSearchParams(window.location.search).get("view")
    || workspace?.dataset.activeWorkspaceView
    || "libraryView";
  const supportedViews = new Set(["editorView", "libraryView", "recordsView", "reportsView"]);
  if (!supportedViews.has(requestedView)) return;

  switchView(requestedView);
  if (requestedView === "recordsView") await loadDetectionRecords();
  if (requestedView === "reportsView") await loadInspectionReports();
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
    if (button.dataset.view === "recordsView") await loadDetectionRecords();
    if (button.dataset.view === "reportsView") await loadInspectionReports();
  });
});

byId("saveRecipe").addEventListener("click", saveRecipe);
byId("publishRecipe").addEventListener("click", publishRecipe);
byId("backToRecipeLibrary").addEventListener("click", () => {
  window.location.href = "/recipes/library";
});
byId("selectRoiDrawMode").addEventListener("click", () => {
  if (state.editorReadOnly) return;
  state.drawMode = "ROI";
  byId("selectRoiDrawMode").classList.add("active");
  byId("selectFeatureAnchorDrawMode").classList.remove("active");
  notify("已切换为 ROI 绘制模式：按住 Ctrl 并拖动鼠标画检测区域。", "info", false);
});
byId("selectFeatureAnchorDrawMode").addEventListener("click", () => {
  if (state.editorReadOnly) return;
  state.drawMode = "FEATURE";
  byId("selectFeatureAnchorDrawMode").classList.add("active");
  byId("selectRoiDrawMode").classList.remove("active");
  notify("已切换为特征点模式：请选择固定螺钉、孔位或 PCB 特征后，按住 Ctrl 并拖动画框。", "info", false);
});
byId("baseImageInput").addEventListener("change", (event) => uploadBaseImage(event.target.files[0]));
byId("emptyImageInput").addEventListener("change", (event) => uploadBaseImage(event.target.files[0]));
byId("imageStage").addEventListener("dragover", (event) => event.preventDefault());
byId("imageStage").addEventListener("drop", (event) => {
  event.preventDefault();
  if (state.editorReadOnly) return;
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
  if (!state.recipe?.base_image_url || !event.ctrlKey) return;
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
byId("configuredObjectList").addEventListener("click", async (event) => {
  const card = event.target.closest("[data-detail-roi]");
  if (!card) return;
  if (event.target.closest(".configured-object-rule-details")) return;
  const roiId = Number(card.dataset.detailRoi);
  if (event.target.closest(".edit-object") || event.target.closest(".view-object")) {
    await openObjectModal(roiId);
    return;
  }
  if (event.target.closest(".delete-object")) {
    if (state.editorReadOnly) return;
    const roi = state.recipe.rois.find((item) => item.id === roiId);
    if (!window.confirm(`确定删除“${roi?.name || "该检测区域"}”及其全部规则吗？`)) return;
    await request(`${api}/configuration/rois/${roiId}`, { method: "DELETE" });
    await loadRecipe(state.recipe.id);
    notify("检测区域及其规则已删除，图片上的对应框已同步移除", "success", false);
    return;
  }
  selectRoi(roiId);
});

byId("objectConfigModal").querySelector(".btn-close").addEventListener("click", closeObjectModal);
byId("saveRoiRules").addEventListener("click", () => saveRoiRules());
byId("testRoiRules").addEventListener("click", testCurrentRoiRules);
byId("roiScenarioVersion").addEventListener("change", (event) => {
  renderRoiScenarioInputRows(event.target.value);
});
byId("addRoiScenarioInput").addEventListener("click", () => {
  const versionId = Number(byId("roiScenarioVersion").value) || null;
  const fields = sceneInputsForVersion(versionId);
  if (!fields.length || state.editorReadOnly) return;
  const container = byId("roiScenarioInputRows");
  container.querySelector(".roi-scenario-input-empty")?.remove();
  container.insertAdjacentHTML("beforeend", roiScenarioInputRowMarkup(fields));
});
byId("roiScenarioInputRows").addEventListener("click", (event) => {
  const button = event.target.closest("[data-remove-roi-scenario-input]");
  if (!button || state.editorReadOnly) return;
  button.closest(".roi-scenario-input-row")?.remove();
  if (!byId("roiScenarioInputRows").children.length) {
    renderRoiScenarioInputRows(byId("roiScenarioVersion").value);
  }
});
byId("featureAnchorStatus").addEventListener("click", async (event) => {
  if (!event.target.closest("#deleteFeatureAnchor") || !state.recipe?.feature_anchor) return;
  if (state.editorReadOnly) return;
  if (!window.confirm("确定删除图像定位特征点吗？后续检测将直接按固定 ROI 裁剪。")) return;
  try {
    await request(`${api}/configuration/recipes/${state.recipe.id}/feature-anchor`, { method: "DELETE" });
    await loadRecipe(state.recipe.id);
    notify("定位特征点已删除。", "success", false);
  } catch (error) {
    notify(error.message, "danger", false);
  }
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
byId("refreshDetectionRecords").addEventListener("click", loadDetectionRecords);
byId("detectionRecordSnFilter").addEventListener("keydown", (event) => {
  if (event.key === "Enter") loadDetectionRecords();
});
byId("detectionRecordsBody").addEventListener("click", (event) => {
  const button = event.target.closest("[data-record-detail]");
  if (!button) return;
  openDetectionRecordDetail(Number(button.dataset.recordDetail));
});
byId("refreshInspectionReports").addEventListener("click", loadInspectionReports);
byId("inspectionReportDimensions").addEventListener("click", (event) => {
  const button = event.target.closest("[data-scene-sort]");
  if (!button) return;
  const key = button.dataset.sceneSort;
  state.reportSceneSort = {
    key,
    direction: state.reportSceneSort.key === key && state.reportSceneSort.direction === "desc"
      ? "asc"
      : "desc",
  };
  renderInspectionReports();
});
byId("inspectionReportDimensions").addEventListener("input", (event) => {
  if (event.target.id !== "sceneReportSearch") return;
  state.reportSceneSearch = event.target.value;
  renderInspectionReports();
  byId("sceneReportSearch")?.focus();
});
byId("configurationLibrary").addEventListener("click", async (event) => {
  const row = event.target.closest("[data-recipe-id]");
  if (!row) return;
  const recipeId = Number(row.dataset.recipeId);
  const productionRecipeId = Number(row.dataset.productionRecipeId) || null;
  const draftRecipeId = Number(row.dataset.draftRecipeId) || null;
  const recipe = state.recipes.find((item) => item.id === recipeId);
  const sourceRecipeId = productionRecipeId || recipeId;
  try {
    if (event.target.closest(".detail-recipe")) {
      window.location.href = `/recipes/editor?recipe_id=${sourceRecipeId}&mode=view`;
    } else if (event.target.closest(".edit-recipe")) {
      const targetId = draftRecipeId || (await request(`${api}/configuration/recipes/${sourceRecipeId}/draft`, { method: "POST" })).id;
      window.location.href = `/recipes/editor?recipe_id=${targetId}`;
    } else if (event.target.closest(".history-recipe")) {
      const history = await request(`${api}/configuration/recipes/${sourceRecipeId}/versions`);
      openRecipeHistory(history, sourceRecipeId);
    } else if (event.target.closest(".copy-recipe")) {
      const copied = await request(`${api}/configuration/recipes/${sourceRecipeId}/copy`, { method: "POST" });
      window.location.href = `/recipes/editor?recipe_id=${copied.id}`;
    } else if (event.target.closest(".test-recipe")) {
      if (!productionRecipeId) {
        notify("该工艺配方尚未发布，不能作为生产配方测试。请在草稿中完成配置后发布。", "warning", false);
        return;
      }
      openTest(productionRecipeId);
    } else if (event.target.closest(".delete-recipe")) {
      if (!draftRecipeId) {
        notify("生产版本不能在此直接删除；如需停用请先创建并发布替代版本。", "warning", false);
        return;
      }
      const draft = state.recipes.find((item) => item.id === draftRecipeId);
      const title = draft?.name || `草稿 #${draftRecipeId}`;
      const warning = `确定删除未发布草稿“${title}”吗？已发布生产版本不会受影响。`;
      if (!window.confirm(warning)) return;
      await request(`${api}/configuration/recipes/${draftRecipeId}`, { method: "DELETE" });
      await loadData();
      notify("未发布草稿已删除，生产版本保持不变。", "success", false);
    }
  } catch (error) {
    notify(error.message, "danger", false);
  }
});

byId("recipeHistoryList").addEventListener("click", async (event) => {
  const openDraft = event.target.closest("[data-recipe-history-open-draft]");
  const rollback = event.target.closest("[data-recipe-history-rollback]");
  if (!openDraft && !rollback) return;
  if (openDraft) {
    window.bootstrap.Modal.getOrCreateInstance(byId("recipeHistoryModal")).hide();
    window.location.href = `/recipes/editor?recipe_id=${Number(openDraft.dataset.recipeHistoryOpenDraft)}`;
    return;
  }
  try {
    const restored = await request(`${api}/configuration/recipes/${Number(rollback.dataset.recipeHistoryRollback)}/rollback`, { method: "POST" });
    window.bootstrap.Modal.getOrCreateInstance(byId("recipeHistoryModal")).hide();
    notify(restored.reused ? "已打开该历史版本对应的待发布草稿。" : "已从历史版本创建待发布草稿；请检查后再发布。", "success", false);
    window.location.href = `/recipes/editor?recipe_id=${restored.id}`;
  } catch (error) {
    notify(error.message, "danger", false);
  }
});

byId("newRecipeFromLibrary").addEventListener("click", () => {
  const modal = new bootstrap.Modal(byId("createRecipeModal"));
  modal.show();
});
byId("createRecipeForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const values = formValues(event.currentTarget);
  const fields = {
    projectName: values.project_name.trim(),
    lineCode: normalizeCode(values.line_code),
    materialCode: normalizeCode(values.material_code),
    processCode: normalizeCode(values.process_code),
    cameraCode: normalizeCode(values.camera_code),
    captureIndex: Math.max(1, Number(values.capture_index || 1)),
    version: String(values.version || "1.0").trim() || "1.0",
  };
  if (!fields.lineCode || !fields.materialCode || !fields.processCode || !fields.cameraCode) {
    notify("请填写拉线、物料号、工序和相机。", "warning", false);
    return;
  }
  const submit = event.currentTarget.querySelector('button[type="submit"]');
  submit.disabled = true;
  submit.textContent = "正在创建…";
  try {
    const product = await ensureProduct(fields.materialCode);
    const station = await ensureStation(fields.lineCode, fields.processCode);
    const generated = generatedRecipe(fields);
    const created = await request(`${api}/configuration/recipes`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        code: generated.code,
        name: generated.name,
        version: fields.version,
        project_name: fields.projectName || null,
        product_id: product.id,
        station_id: station.id,
        line_code: fields.lineCode,
        material_code: fields.materialCode,
        process_code: fields.processCode,
        camera_code: fields.cameraCode,
        capture_index: fields.captureIndex,
      }),
    });
    window.location.href = `/recipes/editor?recipe_id=${created.id}`;
  } catch (error) {
    notify(error.message, "danger", false);
  } finally {
    submit.disabled = false;
    submit.textContent = "创建并进入配置";
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
    state.editorReadOnly = new URLSearchParams(window.location.search).get("mode") === "view";
    await openWorkspaceViewFromQuery();
    const recipeId = Number(new URLSearchParams(window.location.search).get("recipe_id"));
    if (recipeId) {
      switchView("editorView");
      await loadRecipe(recipeId);
    } else if (document.querySelector(".workspace-shell")?.dataset.activeWorkspaceView === "editorView") {
      resetEditor();
    }
  })
  .catch((error) => notify(error.message, "danger"));
initializeReportDateRange();
