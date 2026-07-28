const api = "/api/v1";
const state = {
  products: [],
  stations: [],
  recipes: [],
  references: [],
  details: new Map(),
  recipe: null,
  selectedRoiId: null,
  pendingRect: null,
  drawing: false,
  startPoint: null,
  pointerCandidateRoi: null,
  interactionMode: null,
  workingRect: null,
  originalRect: null,
  savingRoi: false,
  draftRules: [],
  detectionRecords: [],
  testRecipe: null,
  testFile: null,
};

const byId = (id) => document.getElementById(id);
const canvas = byId("roiCanvas");
const context = canvas.getContext("2d");
const baseImage = byId("baseImage");

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
  [state.products, state.stations, state.recipes, state.references] = await Promise.all([
    request(`${api}/configuration/products`),
    request(`${api}/configuration/stations`),
    request(`${api}/configuration/recipes`),
    request(`${api}/configuration/reference-groups`),
  ]);
  const entries = await Promise.all(
    state.recipes.map(async (recipe) => [
      recipe.id,
      await request(`${api}/configuration/recipes/${recipe.id}`),
    ]),
  );
  state.details = new Map(entries);
  fillReferenceSelects();
  renderLibrary();
  if (preferredRecipeId) await loadRecipe(preferredRecipeId);
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
  state.selectedRoiId = null;
  state.pendingRect = null;
  state.workingRect = null;
  state.interactionMode = null;
  byId("recipeForm").reset();
  byId("recipeForm").elements.capture_index.value = "1";
  byId("recipeForm").elements.version.value = "1.0";
  populateRecipeForm(null);
  setRecipeStatus("DRAFT");
  baseImage.removeAttribute("src");
  baseImage.style.display = "none";
  byId("emptyStage").style.display = "grid";
  clearCanvas();
  renderConfiguredObjects();
}

async function loadRecipe(recipeId) {
  state.recipe = await request(`${api}/configuration/recipes/${recipeId}`);
  state.details.set(state.recipe.id, state.recipe);
  state.selectedRoiId = null;
  state.pendingRect = null;
  state.workingRect = null;
  state.interactionMode = null;
  populateRecipeForm(state.recipe);
  setRecipeStatus(state.recipe.status, state.recipe);
  if (state.recipe.base_image_url) {
    baseImage.src = `${state.recipe.base_image_url}?v=${Date.now()}`;
    baseImage.style.display = "block";
    byId("emptyStage").style.display = "none";
  } else {
    baseImage.removeAttribute("src");
    baseImage.style.display = "none";
    byId("emptyStage").style.display = "grid";
    clearCanvas();
  }
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
    await loadRecipe(recipe.id);
    notify(
      result.cleared_roi_count
        ? `图片更换成功，已清除 ${result.cleared_roi_count} 个旧 ROI 及其校验规则，请重新框选`
        : "图片上传成功，请在左侧拖动鼠标框出检测物体",
    );
  } catch (error) {
    notify(error.message, "danger");
  }
}

function syncCanvas() {
  canvas.width = baseImage.clientWidth;
  canvas.height = baseImage.clientHeight;
  canvas.style.width = `${baseImage.clientWidth}px`;
  canvas.style.height = `${baseImage.clientHeight}px`;
  drawCanvas();
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
  const bounds = canvas.getBoundingClientRect();
  return { x: event.clientX - bounds.left, y: event.clientY - bounds.top };
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
  const point = pointerPosition(event);
  const handle = hitResizeHandle(point);
  const existing = hitRoi(point);
  state.drawing = true;
  state.startPoint = point;
  state.pointerCandidateRoi = existing;
  state.pendingRect = null;
  state.workingRect = null;
  if (handle && selectedRoi()) {
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
  if (!state.drawing) return;
  const point = pointerPosition(event);
  const dx = point.x - state.startPoint.x;
  const dy = point.y - state.startPoint.y;
  if (state.interactionMode === "potential-move" && (Math.abs(dx) > 3 || Math.abs(dy) > 3)) {
    state.interactionMode = "move";
  }
  if (state.interactionMode === "draw") {
    state.pendingRect = clampRect({
      x: Math.min(state.startPoint.x, point.x),
      y: Math.min(state.startPoint.y, point.y),
      width: Math.abs(dx),
      height: Math.abs(dy),
    });
  } else if (state.interactionMode === "move") {
    state.workingRect = clampRect({
      ...state.originalRect,
      x: state.originalRect.x + dx,
      y: state.originalRect.y + dy,
    });
  } else if (state.interactionMode?.startsWith("resize-")) {
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

canvas.addEventListener("pointerup", async () => {
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
  state.pendingRect = null;
  state.workingRect = null;
  if (candidate) {
    selectRoi(candidate.id);
  } else {
    drawCanvas();
  }
});

baseImage.addEventListener("load", syncCanvas);
window.addEventListener("resize", () => {
  if (baseImage.src) syncCanvas();
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
    const roiId = roi.id;
    state.workingRect = null;
    await loadRecipe(state.recipe.id);
    selectRoi(roiId);
    notify("检测区域位置和大小已自动保存", "success", false);
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
};

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
            <div>
              <small>${escapeHtml(roi.code)} · ${escapeHtml(roi.object_type || "OBJECT")}</small>
              <strong>${escapeHtml(roi.name)}</strong>
            </div>
            <b>${roi.inspection_items.length} 条规则</b>
          </div>
          <div class="configured-object-rules">
            ${roi.inspection_items.length
              ? roi.inspection_items.map((item) => `
                  <span>${escapeHtml(capabilityMeta[itemCapability(item)]?.[0] || item.capability)}-${escapeHtml(describeRule(item))}</span>`).join("")
              : "<em>尚未配置校验规则</em>"}
          </div>
          <div class="configured-object-actions">
            <button class="btn btn-sm btn-outline-primary edit-object" type="button">编辑规则</button>
            <button class="btn btn-sm btn-outline-danger delete-object" type="button">删除区域</button>
          </div>
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
  byId("roiRuleStatus").textContent = "";
  byId("roiRuleStatus").className = "roi-rule-status";
  byId("selectedObjectTitle").textContent = roi.name;
  const rect = roiRect(roi);
  byId("roiCode").value = roi.code;
  byId("roiPointX").value = `${Math.round(rect.x)} px`;
  byId("roiPointY").value = `${Math.round(rect.y)} px`;
  byId("roiPointWidth").value = `${Math.round(rect.width)} px`;
  byId("roiPointHeight").value = `${Math.round(rect.height)} px`;
  updateRoiReferencePreview(roi);
  const configuredRules = roi.inspection_items.map((item) => {
    const capability = itemCapability(item);
    if (capability === "PRESENCE" || capability === "EXISTENCE" || item.capability === "REFERENCE_SIMILARITY") {
      return {
        type: "EXISTENCE",
        value: String(item.rule_json.min_similarity ?? 0.9),
        locked: true,
      };
    }
    if (capability === "COLOR_RATIO") {
      return { type: "COLOR", value: String(item.expected_json.color || "").toLowerCase() };
    }
    return { type: "TEXT", value: String(item.expected_json.text || "") };
  });
  const existenceRule = configuredRules.find((rule) => rule.type === "EXISTENCE");
  state.draftRules = [
    existenceRule || { type: "EXISTENCE", value: "0.9", locked: true },
    ...configuredRules.filter((rule) => rule.type !== "EXISTENCE"),
  ];
  renderRuleRows();
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
  if (type === "COLOR") return "例如 yellow";
  return "输入需要校验的文字";
}

function renderRuleRows() {
  byId("ruleRows").innerHTML = state.draftRules.length
    ? state.draftRules.map((rule, index) => `
        <div class="rule-table-row" data-rule-index="${index}">
          <span class="rule-row-index">${index + 1}</span>
          <select class="form-select rule-row-type" ${rule.locked ? "disabled" : ""}>
            ${rule.locked ? '<option value="EXISTENCE" selected>存在校验</option>' : ""}
            <option value="COLOR" ${rule.type === "COLOR" ? "selected" : ""}>颜色校验</option>
            <option value="TEXT" ${rule.type === "TEXT" ? "selected" : ""}>文本校验</option>
          </select>
          <input class="form-control rule-row-value" value="${escapeHtml(rule.value)}" placeholder="${ruleValuePlaceholder(rule.type)}" ${rule.type === "EXISTENCE" ? 'type="number" min="0" max="1" step="0.01"' : ""}>
          ${rule.locked
            ? '<span class="default-rule-label">默认规则</span>'
            : '<button class="btn btn-sm btn-outline-danger remove-rule-row" type="button">删除</button>'}
        </div>`).join("")
    : `
      <div class="empty-rule-state table-empty">
        <span>＋</span><strong>尚未配置校验规则</strong>
        <p>点击“添加规则”新增一行校验项目。</p>
      </div>`;
}

function addRuleRow() {
  state.draftRules.push({ type: "COLOR", value: "" });
  renderRuleRows();
}

async function saveRoiRules() {
  const roi = selectedRoi();
  if (!roi) return;
  for (const rule of state.draftRules) {
    const value = rule.value.trim();
    if (!value) return notify("每条规则都必须填写校验值", "warning", false);
    if (rule.type === "EXISTENCE" && (!Number.isFinite(Number(value)) || Number(value) <= 0 || Number(value) > 1)) {
      return notify("存在校验的相似度阈值必须大于 0 且不超过 1", "warning", false);
    }
    if (rule.type === "COLOR" && !/^[a-zA-Z]+$/.test(value)) {
      return notify("颜色校验请填写英文颜色名称，例如 yellow", "warning", false);
    }
  }

  const saveButton = byId("saveRoiRules");
  const status = byId("roiRuleStatus");
  saveButton.disabled = true;
  saveButton.textContent = "正在保存…";
  status.textContent = "正在保存规则并生成标准参考图…";
  status.className = "roi-rule-status working";
  try {
    const reference = await request(`${api}/configuration/rois/${roi.id}/capture-reference`, {
      method: "POST",
    });
    for (const item of roi.inspection_items) {
      await request(`${api}/configuration/inspection-items/${item.id}`, { method: "DELETE" });
    }
    for (const [index, rule] of state.draftRules.entries()) {
      const value = rule.value.trim();
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
          rule_json: { min_similarity: Number(value) },
        };
      } else if (rule.type === "COLOR") {
        payload = {
          inspection_type: "COLOR",
          capability: "COLOR_RATIO",
          reference_group_id: null,
          expected_json: { color: value.toUpperCase() },
          rule_json: { min_ratio: 0.15, max_ratio: 1 },
        };
      } else {
        payload = {
          inspection_type: "TEXT",
          capability: "OCR_TEXT",
          reference_group_id: null,
          expected_json: { text: value },
          rule_json: { operator: "CONTAINS", case_sensitive: false },
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
    const roiId = roi.id;
    await loadRecipe(state.recipe.id);
    selectRoi(roiId);
    populateObjectEditor();
    status.textContent = "保存成功";
    status.className = "roi-rule-status success";
    notify("当前 ROI 的校验规则已保存", "success", false);
  } catch (error) {
    status.textContent = `保存失败：${error.message}`;
    status.className = "roi-rule-status error";
    notify(error.message, "danger", false);
  } finally {
    saveButton.disabled = false;
    saveButton.textContent = "保存当前 ROI 规则";
  }
}

function renderLibrary() {
  const query = byId("librarySearch")?.value.trim().toLowerCase() || "";
  const filtered = state.recipes.filter((recipe) => {
    const detail = state.details.get(recipe.id);
    return !query || JSON.stringify({ ...recipe, ...detail }).toLowerCase().includes(query);
  });
  byId("configurationLibrary").innerHTML = filtered.length
    ? filtered.map((recipe) => `
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
    : '<div class="library-no-results">没有找到匹配的规则配方</div>';
}

function formatDetectionTime(value) {
  if (!value) return "-";
  const utcValue = /(?:Z|[+-]\d{2}:\d{2})$/.test(value) ? value : `${value}Z`;
  return new Date(utcValue).toLocaleString("zh-CN", { hour12: false });
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
    : '<tr><td colspan="7" class="records-empty">暂无第三方检测调用记录</td></tr>';
}

async function loadDetectionRecords() {
  byId("detectionRecordsBody").innerHTML =
    '<tr><td colspan="7" class="records-empty">正在加载检测记录…</td></tr>';
  try {
    state.detectionRecords = await request(`${api}/inspection/call-records?limit=200`);
    renderDetectionRecords();
  } catch (error) {
    byId("detectionRecordsBody").innerHTML =
      `<tr><td colspan="7" class="records-empty error">${escapeHtml(error.message)}</td></tr>`;
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

function openTest(recipeId) {
  state.testRecipe = state.details.get(recipeId);
  state.testFile = null;
  byId("testRecipeName").textContent = state.testRecipe.name;
  byId("testFile").value = "";
  byId("testPreviewImage").hidden = true;
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
  preview.hidden = false;
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
    const status = roiItems.some((item) => item.status === "ERROR")
      ? "ERROR"
      : roiItems.some((item) => item.status === "NG") ? "NG" : "OK";
    return `
      <section class="test-object-card">
        <div class="test-object-card-heading">
          <div><small>${escapeHtml(roiCode)}</small><strong>${escapeHtml(roi?.name || roiCode)}</strong></div>
          <span class="result-badge ${status.toLowerCase()}">${status}</span>
        </div>
        <div class="test-rule-details">
          ${roiItems.map((item) => `
            <div class="test-rule-row">
              <span class="rule-result-icon ${item.status.toLowerCase()}">${item.status === "OK" ? "✓" : "!"}</span>
              <div><strong>${escapeHtml(item.item_name)}</strong><small>${escapeHtml(item.message)}</small></div>
              <b>${item.score == null ? item.status : Number(item.score).toFixed(4)}</b>
            </div>`).join("")}
        </div>
      </section>`;
  }).join("") || '<div class="library-no-results">该配方没有可执行规则</div>';
  byId("testPreviewImage").src = `/results/${result.request_id}/result_1.jpg?v=${Date.now()}`;
}

async function runTest() {
  if (!state.testRecipe || !state.testFile) return;
  const data = new FormData();
  data.append("recipe_id", state.testRecipe.id);
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
  });
});

byId("newRecipe").addEventListener("click", resetEditor);
byId("saveRecipe").addEventListener("click", saveRecipe);
byId("baseImageInput").addEventListener("change", (event) => uploadBaseImage(event.target.files[0]));
byId("emptyImageInput").addEventListener("change", (event) => uploadBaseImage(event.target.files[0]));
byId("imageStage").addEventListener("dragover", (event) => event.preventDefault());
byId("imageStage").addEventListener("drop", (event) => {
  event.preventDefault();
  uploadBaseImage(event.dataTransfer.files[0]);
});

byId("configuredObjectList").addEventListener("click", async (event) => {
  const card = event.target.closest("[data-detail-roi]");
  if (!card) return;
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

byId("objectConfigModal").querySelector(".btn-close").addEventListener("click", closeObjectModal);
byId("addRuleRow").addEventListener("click", addRuleRow);
byId("saveRoiRules").addEventListener("click", saveRoiRules);
byId("ruleRows").addEventListener("change", (event) => {
  const row = event.target.closest("[data-rule-index]");
  if (!row || !event.target.classList.contains("rule-row-type")) return;
  const index = Number(row.dataset.ruleIndex);
  const type = event.target.value;
  state.draftRules[index].type = type;
  state.draftRules[index].value = "";
  renderRuleRows();
});
byId("ruleRows").addEventListener("input", (event) => {
  const row = event.target.closest("[data-rule-index]");
  if (!row || !event.target.classList.contains("rule-row-value")) return;
  state.draftRules[Number(row.dataset.ruleIndex)].value = event.target.value;
});
byId("ruleRows").addEventListener("click", (event) => {
  const button = event.target.closest(".remove-rule-row");
  if (!button) return;
  const row = button.closest("[data-rule-index]");
  state.draftRules.splice(Number(row.dataset.ruleIndex), 1);
  renderRuleRows();
});

byId("librarySearch").addEventListener("input", renderLibrary);
byId("refreshDetectionRecords").addEventListener("click", loadDetectionRecords);
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
byId("runRecipeTest").addEventListener("click", runTest);

loadData()
  .then(async () => {
    if (state.recipes.length) await loadRecipe(state.recipes[0].id);
    else resetEditor();
  })
  .catch((error) => notify(error.message, "danger"));
