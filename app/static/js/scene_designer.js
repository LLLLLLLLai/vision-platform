(() => {
  const api = "/api/v1";
  const canvasWidth = 2200;
  const canvasHeight = 1320;
  const subflowWidth = 1280;
  const subflowHeight = 620;
  const nodeWidth = 224;
  const nodeHeight = 96;
  const byId = (id) => document.getElementById(id);
  const sceneId = Number(byId("sceneDesigner").dataset.sceneId);
  const state = {
    scene: null,
    versionId: null,
    vlms: [],
    publishedScenes: [],
    visionVersions: [],
    sceneApiContract: null,
    selectedNodeId: null,
    selectedEdgeId: null,
    zoom: 1,
    drag: null,
    connecting: null,
    previewUrl: null,
    workflowTestPreviewUrl: null,
    fittedWorkflowVersions: new Set(),
    variableTarget: null,
    promptSelection: null,
    variablePickerQuery: "",
    paletteCollapsed: false,
    inspectorCollapsed: true,
    inspectorTab: "SETTINGS",
    directTestValues: {},
    pendingNodeKeys: new Set(),
    loopBodyDraft: null,
    loopBodyNodeId: null,
    subflowNodeId: null,
  };

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
      const error = new Error(detail);
      error.status = response.status;
      throw error;
    }
    return payload;
  };
  const notify = (message, type = "success") => {
    byId("designerAlert").innerHTML = `<div class="alert alert-${type} alert-dismissible fade show">${escapeHtml(message)}<button class="btn-close" data-bs-dismiss="alert"></button></div>`;
  };
  const currentVersion = () => state.scene?.versions.find((item) => item.id === state.versionId) || null;
  const isDraft = () => currentVersion()?.status === "DRAFT";
  const modeLabel = (mode) => mode === "WORKFLOW" ? "流程检测" : "VLM 检测";
  const nodeTypeLabel = (type) => ({
    START: "开始", END: "结束", VLM: "VLM 检测", VISION_MODEL: "训练模型", IMAGE_CROP: "图片裁剪", IMAGE_ANNOTATE: "图片画框", RULE: "规则判断", WEB_API: "Web 接口", S3_UPLOAD: "S3 上传", IF: "条件分支", LOOP: "旧版循环", ITERATION: "遍历处理", CONDITION_LOOP: "条件循环",
  }[String(type || "").toUpperCase()] || type);
  const nodeTypeIcon = (type) => ({
    START: "▶", END: "■", VLM: "◇", VISION_MODEL: "◉", IMAGE_CROP: "✂", IMAGE_ANNOTATE: "▣", RULE: "✓", WEB_API: "↗", S3_UPLOAD: "☁", IF: "?", LOOP: "↻", ITERATION: "⇢", CONDITION_LOOP: "↻",
  }[String(type || "").toUpperCase()] || "◇");
  const statusClass = (status) => {
    const value = String(status || "DRAFT").toLowerCase();
    return ["published", "completed"].includes(value) ? "published" : ["failed", "canceled"].includes(value) ? "error" : "draft";
  };
  const deepCopy = (value) => JSON.parse(JSON.stringify(value || {}));

  function schemaFields(schema) {
    const raw = Array.isArray(schema)
      ? schema
      : (schema?.fields || schema?.inputs || []);
    return Array.isArray(raw) ? raw.filter((item) => item && typeof item === "object") : [];
  }

  function formatInputFields(schema) {
    return schemaFields(schema)
      .filter((field) => !imageInputNames.has(String(field.name || "").toLowerCase()))
      .map((field) => `${field.name}${field.label && field.label !== field.name ? ` | ${field.label}` : ""}`)
      .join("\n");
  }

  function parseInputFields(value) {
    const seen = new Set();
    const fields = [];
    String(value || "").split(/\r?\n/).forEach((line) => {
      const trimmed = line.trim();
      if (!trimmed) return;
      const [rawName, ...labelParts] = trimmed.split("|");
      const name = rawName.trim();
      const label = labelParts.join("|").trim() || name;
      if (!/^[A-Za-z_][A-Za-z0-9_]*$/.test(name)) {
        throw new Error(`场景字段“${name}”只能使用字母、数字和下划线，且不能以数字开头。`);
      }
      if (imageInputNames.has(name.toLowerCase())) {
        throw new Error(`“${name}”由系统自动传入，不能重复声明。`);
      }
      if (seen.has(name)) throw new Error(`场景字段“${name}”重复。`);
      seen.add(name);
      fields.push({ name, label, type: "TEXT", required: false });
    });
    return fields;
  }

  const imageInputNames = new Set(["image", "image_path", "image_paths", "image_file", "original_image_path", "source_image_path", "roi_bbox", "result_s3_key"]);
  const parameterKeyPattern = /^[A-Za-z_][A-Za-z0-9_]*$/;

  function parameterValue(value) {
    if (value === null || value === undefined) return "";
    return typeof value === "string" ? value : JSON.stringify(value);
  }

  function mappingRows(value) {
    if (Array.isArray(value)) {
      return value.map((item) => ({
        key: String(item?.key ?? item?.name ?? ""),
        value: parameterValue(item?.value ?? item?.label ?? ""),
      }));
    }
    if (!value || typeof value !== "object") return [];
    return Object.entries(value).map(([key, item]) => ({ key, value: parameterValue(item) }));
  }

  function schemaRows(schema) {
    return schemaFields(schema)
      .filter((field) => !imageInputNames.has(String(field.name || "").toLowerCase()))
      .map((field) => ({ key: String(field.name || ""), value: String(field.label || field.name || "") }));
  }

  function variableControlMarkup({
    id = "",
    value = "",
    placeholder = "",
    readonly = false,
    className = "form-control",
    scope = "INPUT",
    mode = "REPLACE",
    ariaLabel = "参数值",
    type = "text",
    rows = null,
    acceptedTypes = "",
    dataAttributes = "",
  } = {}) {
    const fieldId = id ? ` id="${escapeHtml(id)}"` : "";
    const acceptedTypesAttribute = String(acceptedTypes || "").trim()
      ? ` data-variable-accept-types="${escapeHtml(String(acceptedTypes).trim())}"`
      : "";
    const field = rows
      ? `<textarea${fieldId} class="${escapeHtml(className)}" data-variable-target data-variable-scope="${escapeHtml(scope)}" data-variable-mode="${escapeHtml(mode)}"${acceptedTypesAttribute} ${dataAttributes} aria-label="${escapeHtml(ariaLabel)}" placeholder="${escapeHtml(placeholder)}" rows="${Number(rows)}" ${readonly ? "readonly" : ""}>${escapeHtml(value)}</textarea>`
      : `<input${fieldId} class="${escapeHtml(className)}" data-variable-target data-variable-scope="${escapeHtml(scope)}" data-variable-mode="${escapeHtml(mode)}"${acceptedTypesAttribute} ${dataAttributes} aria-label="${escapeHtml(ariaLabel)}" value="${escapeHtml(value)}" placeholder="${escapeHtml(placeholder)}" type="${escapeHtml(type)}" ${readonly ? "readonly" : ""}>`;
    const trigger = readonly ? "" : '<button class="variable-picker-trigger" type="button" data-variable-picker title="选择变量" aria-label="选择变量"><span>{ }</span></button>';
    return `<div class="variable-control">${field}${trigger}</div>`;
  }

  function variableFieldMarkup({ label, ...options }) {
    return `<label class="variable-input-field"><span>${escapeHtml(label)}</span>${variableControlMarkup(options)}</label>`;
  }

  function promptVariableTokenLabel(token) {
    return String(token || "").replace(/^\{\{\s*|\s*\}\}$/g, "").trim();
  }

  function promptTemplateTextMarkup(value) {
    return escapeHtml(value).replace(/\r?\n/g, "<br>");
  }

  function promptVariableChipMarkup(token, readonly = false) {
    const safeToken = escapeHtml(token);
    const safeLabel = escapeHtml(promptVariableTokenLabel(token));
    const remove = readonly
      ? ""
      : '<button class="prompt-variable-chip-remove" type="button" data-prompt-variable-remove title="删除变量" aria-label="删除变量">×</button>';
    return `<span class="prompt-variable-chip" contenteditable="false" data-prompt-variable-token="${safeToken}"><span class="prompt-variable-chip-label" title="${safeToken}">${safeLabel}</span>${remove}</span>`;
  }

  function promptTemplateMarkup(value, readonly = false) {
    const pattern = /\{\{\s*[^{}\r\n]+?\s*\}\}/g;
    const text = String(value || "");
    let cursor = 0;
    let markup = "";
    let match;
    while ((match = pattern.exec(text)) !== null) {
      markup += promptTemplateTextMarkup(text.slice(cursor, match.index));
      markup += promptVariableChipMarkup(match[0], readonly);
      cursor = match.index + match[0].length;
    }
    return `${markup}${promptTemplateTextMarkup(text.slice(cursor))}`;
  }

  function promptTemplateEditorMarkup({
    id,
    value = "",
    placeholder = "",
    readonly = false,
    ariaLabel = "检测提示词",
  } = {}) {
    const fieldId = `${id}Editor`;
    const trigger = readonly
      ? ""
      : '<button class="variable-picker-trigger" type="button" data-variable-picker title="选择变量" aria-label="选择变量"><span>{ }</span></button>';
    return `<div class="prompt-template-control variable-control"><div id="${escapeHtml(fieldId)}" class="prompt-template-editor${readonly ? " is-readonly" : ""}" contenteditable="${readonly ? "false" : "true"}" role="textbox" aria-multiline="true" aria-label="${escapeHtml(ariaLabel)}" data-variable-target data-prompt-template-editor data-prompt-field="${escapeHtml(id)}" data-variable-scope="PROMPT" data-variable-mode="INSERT" data-placeholder="${escapeHtml(placeholder)}">${promptTemplateMarkup(value, readonly)}</div><input id="${escapeHtml(id)}" type="hidden" value="${escapeHtml(value)}">${trigger}</div>`;
  }

  function promptTemplateFieldMarkup({ label, ...options }) {
    return `<label class="variable-input-field prompt-template-field"><span>${escapeHtml(label)}</span>${promptTemplateEditorMarkup(options)}</label>`;
  }

  function isPromptTemplateEditor(target) {
    return Boolean(target?.matches?.("[data-prompt-template-editor]"));
  }

  function serializePromptTemplate(editor) {
    if (!editor) return "";
    const readNode = (node) => {
      if (node.nodeType === Node.TEXT_NODE) return node.nodeValue || "";
      if (node.nodeType !== Node.ELEMENT_NODE) return "";
      const element = node;
      if (element.matches?.("[data-prompt-variable-token]")) return element.dataset.promptVariableToken || "";
      if (element.tagName === "BR") return "\n";
      const content = [...element.childNodes].map(readNode).join("");
      return ["DIV", "P"].includes(element.tagName) && element !== editor && element.nextSibling ? `${content}\n` : content;
    };
    return [...editor.childNodes].map(readNode).join("").replaceAll("\u200B", "");
  }

  function syncPromptTemplateEditor(editor) {
    const value = serializePromptTemplate(editor);
    const field = byId(editor?.dataset?.promptField);
    if (field) field.value = value;
    return value;
  }

  function capturePromptSelection(editor) {
    if (!isPromptTemplateEditor(editor)) return;
    const selection = window.getSelection();
    if (!selection?.rangeCount) return;
    const range = selection.getRangeAt(0);
    if (!editor.contains(range.commonAncestorContainer)) return;
    state.promptSelection = { editor, range: range.cloneRange() };
  }

  function restorePromptSelection(editor) {
    const selection = window.getSelection();
    const saved = state.promptSelection;
    let range = null;
    if (saved?.editor === editor && editor.contains(saved.range.commonAncestorContainer)) range = saved.range.cloneRange();
    if (!range) {
      range = document.createRange();
      range.selectNodeContents(editor);
      range.collapse(false);
    }
    selection?.removeAllRanges();
    selection?.addRange(range);
    return range;
  }

  function createPromptVariableChip(token) {
    const chip = document.createElement("span");
    chip.className = "prompt-variable-chip";
    chip.contentEditable = "false";
    chip.dataset.promptVariableToken = token;
    const label = document.createElement("span");
    label.className = "prompt-variable-chip-label";
    label.title = token;
    label.textContent = promptVariableTokenLabel(token);
    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "prompt-variable-chip-remove";
    remove.dataset.promptVariableRemove = "";
    remove.title = "删除变量";
    remove.setAttribute("aria-label", "删除变量");
    remove.textContent = "×";
    chip.append(label, remove);
    return chip;
  }

  function insertPromptVariableChip(token, editor) {
    if (!isPromptTemplateEditor(editor)) return false;
    editor.focus();
    const range = restorePromptSelection(editor);
    range.deleteContents();
    const chip = createPromptVariableChip(token);
    const separator = document.createTextNode("\u200B");
    range.insertNode(separator);
    range.insertNode(chip);
    const nextRange = document.createRange();
    nextRange.setStartAfter(separator);
    nextRange.collapse(true);
    const selection = window.getSelection();
    selection?.removeAllRanges();
    selection?.addRange(nextRange);
    syncPromptTemplateEditor(editor);
    capturePromptSelection(editor);
    state.variableTarget = editor;
    return true;
  }

  function parameterRowMarkup(containerId, row = {}, readonly = false, variableScope = null) {
    const container = byId(containerId);
    const keyPlaceholder = container?.dataset.keyPlaceholder || "例如 expected_text";
    const valuePlaceholder = container?.dataset.valuePlaceholder || "例如 {{ input.expected_text }}";
    const disabled = readonly ? "disabled" : "";
    const scope = variableScope || container?.dataset.variableScope || "NONE";
    const valueField = scope === "NONE"
      ? `<input class="form-control form-control-sm node-parameter-value" data-parameter-value aria-label="参数值" value="${escapeHtml(row.value || "")}" placeholder="${escapeHtml(valuePlaceholder)}" ${disabled}>`
      : variableControlMarkup({
        value: row.value || "",
        placeholder: valuePlaceholder,
        readonly,
        className: "form-control form-control-sm node-parameter-value",
        scope,
        ariaLabel: "参数值",
        dataAttributes: "data-parameter-value",
      });
    return `<div class="node-parameter-row" data-parameter-row>
      <input class="form-control form-control-sm" data-parameter-key aria-label="参数键" value="${escapeHtml(row.key || "")}" placeholder="${escapeHtml(keyPlaceholder)}" ${disabled}>
      ${valueField}
      ${scope === "NONE" ? '<span class="node-parameter-variable-placeholder" aria-hidden="true"></span>' : ""}
      ${readonly ? '<span class="node-parameter-readonly" aria-hidden="true">—</span>' : `<button class="node-parameter-remove" type="button" data-parameter-remove="${escapeHtml(containerId)}" title="删除参数" aria-label="删除参数">×</button>`}
    </div>`;
  }

  function renderParameterRows(containerId, rows, { kind = "MAPPING", readonly = false, keyPlaceholder, valuePlaceholder, variableScope = "NONE" } = {}) {
    const container = byId(containerId);
    if (!container) return;
    container.dataset.parameterKind = kind;
    container.dataset.variableScope = variableScope;
    if (keyPlaceholder) container.dataset.keyPlaceholder = keyPlaceholder;
    if (valuePlaceholder) container.dataset.valuePlaceholder = valuePlaceholder;
    container.innerHTML = (rows || []).map((row) => parameterRowMarkup(containerId, row, readonly, variableScope)).join("");
  }

  function parameterSectionMarkup({
    id,
    title,
    description,
    rows,
    kind = "MAPPING",
    readonly = false,
    keyLabel = "参数键",
    valueLabel = "参数值",
    keyPlaceholder = "例如 expected_text",
    valuePlaceholder = "例如 {{ input.expected_text }}",
    variableScope = "INPUT",
  }) {
    const addButton = readonly ? "" : `<button class="node-parameter-add" type="button" data-parameter-add="${escapeHtml(id)}" title="添加参数"><span aria-hidden="true">＋</span>添加参数</button>`;
    const variableColumn = '<span aria-hidden="true"></span>';
    return `<section class="node-parameter-section" data-variable-scope="${escapeHtml(variableScope)}">
      <div class="node-parameter-heading"><div><strong>${escapeHtml(title)}</strong>${description ? `<small>${escapeHtml(description)}</small>` : ""}</div>${addButton}</div>
      <div class="node-parameter-columns"><span>${escapeHtml(keyLabel)}</span><span>${escapeHtml(valueLabel)}</span>${variableColumn}<span aria-hidden="true"></span></div>
      <div class="node-parameter-rows" id="${escapeHtml(id)}" data-parameter-kind="${escapeHtml(kind)}" data-variable-scope="${escapeHtml(variableScope)}" data-key-placeholder="${escapeHtml(keyPlaceholder)}" data-value-placeholder="${escapeHtml(valuePlaceholder)}">${(rows || []).map((row) => parameterRowMarkup(id, row, readonly, variableScope)).join("")}</div>
    </section>`;
  }

  function addParameterRow(containerId) {
    const container = byId(containerId);
    if (!container) return;
    container.insertAdjacentHTML("beforeend", parameterRowMarkup(containerId, {}, false, container.dataset.variableScope || "NONE"));
    container.querySelector("[data-parameter-row]:last-child [data-parameter-key]")?.focus();
  }

  function removeParameterRow(button) {
    button.closest("[data-parameter-row]")?.remove();
  }

  function readParameterRows(containerId, label) {
    const container = byId(containerId);
    if (!container) return {};
    const values = {};
    const seen = new Set();
    container.querySelectorAll("[data-parameter-row]").forEach((row) => {
      const key = row.querySelector("[data-parameter-key]")?.value.trim() || "";
      const value = row.querySelector("[data-parameter-value]")?.value.trim() || "";
      if (!key && !value) return;
      if (!key) throw new Error(`${label}的参数键不能为空。`);
      if (!parameterKeyPattern.test(key)) throw new Error(`${label}的参数键“${key}”只能使用字母、数字和下划线，且不能以数字开头。`);
      if (seen.has(key)) throw new Error(`${label}的参数键“${key}”重复。`);
      seen.add(key);
      values[key] = value;
    });
    return values;
  }

  function readSchemaParameterRows(containerId, label) {
    const parameters = readParameterRows(containerId, label);
    return Object.entries(parameters).map(([name, fieldLabel]) => {
      if (imageInputNames.has(name.toLowerCase())) throw new Error(`“${name}”由系统自动传入，不能重复声明。`);
      return { name, label: fieldLabel || name, type: "TEXT", required: false };
    });
  }

  function workflowInputFields(version = currentVersion()) {
    const start = version?.nodes?.find((node) => node.enabled && node.node_type === "START");
    return schemaFields(start?.config_json?.inputs || []);
  }

  function updateHeader() {
    const version = currentVersion();
    if (!state.scene || !version) return;
    byId("designerSceneCode").textContent = state.scene.code;
    byId("designerSceneName").textContent = state.scene.name;
    byId("designerSceneMeta").textContent = `${state.scene.category || "GENERAL"} · ${modeLabel(state.scene.mode)} · ${state.scene.description || "未填写说明"}`;
    byId("designerVersionSelect").innerHTML = state.scene.versions.map((item) => `<option value="${item.id}" ${item.id === version.id ? "selected" : ""}>V${escapeHtml(item.version)} · ${escapeHtml(item.status)}</option>`).join("");
    const status = byId("designerVersionStatus");
    status.textContent = version.status;
    status.className = `status-pill ${statusClass(version.status)}`;
    const saveButton = byId("designerSave");
    saveButton.disabled = !isDraft();
    saveButton.textContent = state.scene.mode === "WORKFLOW" ? "保存当前节点" : "保存草稿";
    saveButton.title = state.scene.mode === "WORKFLOW" ? "保存当前选中的节点配置" : "保存当前草稿";
    byId("designerPublish").hidden = !isDraft();
    byId("designerClone").disabled = false;
    const validateButton = byId("designerValidate");
    validateButton.textContent = state.scene.mode === "WORKFLOW" ? "检查编排" : "检查配置";
    validateButton.title = state.scene.mode === "WORKFLOW"
      ? "检查节点、连线、变量来源和模型配置"
      : "检查主模型、提示词和输入参数";
    validateButton.disabled = false;
    const apiButton = byId("designerApi");
    const hasPublishedVersion = Boolean(state.scene.published_version_id);
    apiButton.disabled = !hasPublishedVersion;
    apiButton.title = hasPublishedVersion
      ? "查看当前已发布版本的外部调用接口"
      : "请先发布场景后再查看外部调用接口";
  }

  function renderVersionHistory() {
    const container = byId("sceneVersionList");
    const versions = state.scene?.versions || [];
    container.innerHTML = versions.length ? versions.map((version) => {
      const selected = Number(version.id) === Number(state.versionId);
      const modeSummary = state.scene?.mode === "WORKFLOW"
        ? `${(version.nodes || []).filter((node) => node.enabled).length} 个启用节点`
        : (version.prompt_template ? "已配置检测提示词" : "尚未配置检测提示词");
      const time = version.published_at || version.updated_at || version.created_at || "";
      const timeText = time ? new Date(time).toLocaleString("zh-CN", { hour12: false }) : "未发布";
      return `<article class="scene-version-row${selected ? " current" : ""}"><div><strong>V${escapeHtml(version.version)} · ${escapeHtml(version.status)}</strong><small>${escapeHtml(modeSummary)} · ${escapeHtml(timeText)}</small></div><div class="scene-version-row-meta"><span class="status-pill ${statusClass(version.status)}">${escapeHtml(version.status)}</span><button class="btn btn-sm ${selected ? "btn-primary" : "btn-outline-primary"}" type="button" data-select-scene-version="${version.id}">${selected ? "当前查看" : "查看此版本"}</button></div></article>`;
    }).join("") : '<div class="empty-state compact-empty"><strong>暂无版本记录</strong></div>';
  }

  function openVersionHistory() {
    renderVersionHistory();
    const modalElement = byId("sceneVersionModal");
    const modal = window.bootstrap?.Modal?.getOrCreateInstance(modalElement);
    if (!modal) { notify("页面缺少版本历史弹窗组件，请刷新后重试。", "warning"); return; }
    modal.show();
  }

  function selectSceneVersion(versionId, { closeHistory = false } = {}) {
    if (!state.scene?.versions.some((item) => Number(item.id) === Number(versionId))) return;
    closeSubflowCanvas();
    state.versionId = Number(versionId);
    state.selectedNodeId = null;
    state.selectedEdgeId = null;
    clearWorkflowTest();
    renderAll();
    if (closeHistory) window.bootstrap?.Modal?.getInstance(byId("sceneVersionModal"))?.hide();
  }

  function sceneApiEndpointUrl(contract) {
    return `${window.location.origin}${contract?.endpoint_path || ""}`;
  }

  async function copyTextToClipboard(value) {
    const text = String(value || "");
    if (!text) throw new Error("没有可复制的内容。");
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text);
      return;
    }
    const fallback = document.createElement("textarea");
    fallback.value = text;
    fallback.setAttribute("readonly", "");
    fallback.style.position = "fixed";
    fallback.style.opacity = "0";
    document.body.append(fallback);
    fallback.select();
    const copied = document.execCommand("copy");
    fallback.remove();
    if (!copied) throw new Error("浏览器不支持自动复制，请手动复制。");
  }

  function renderSceneApiFields(fields) {
    const container = byId("sceneApiFields");
    const items = Array.isArray(fields) ? fields : [];
    container.innerHTML = items.map((field) => {
      const label = field.label || field.name;
      const required = field.required ? " required" : "";
      return `<span class="scene-api-field${required}"><b>${escapeHtml(label)}</b><code>${escapeHtml(field.name)}</code></span>`;
    }).join("");
  }

  async function showSceneApiContract() {
    if (!state.scene?.published_version_id) {
      notify("请先发布场景后再查看外部调用接口。", "warning");
      return;
    }
    try {
      const contract = await request(`${api}/scenarios/invoke/${encodeURIComponent(state.scene.code)}`);
      state.sceneApiContract = contract;
      const authentication = contract.authentication || {};
      const version = contract.scene?.version || "—";
      byId("sceneApiDescription").textContent = `${contract.scene?.name || state.scene.name} · V${version}。外部请求始终调用该场景当前已发布的版本，不会执行草稿或归档版本。${authentication.description ? ` ${authentication.description}` : ""}`;
      byId("sceneApiEndpoint").textContent = sceneApiEndpointUrl(contract);
      byId("sceneApiRequestExample").textContent = JSON.stringify(contract.request_example || {}, null, 2);
      byId("sceneApiResponseExample").textContent = JSON.stringify(contract.response_example || {}, null, 2);
      renderSceneApiFields(contract.input_fields);
      const modalElement = byId("sceneApiModal");
      const modal = window.bootstrap?.Modal?.getOrCreateInstance(modalElement);
      if (!modal) throw new Error("页面缺少弹窗组件，请刷新后重试。");
      modal.show();
    } catch (error) {
      notify(error.message, "danger");
    }
  }

  async function copySceneApiValue(kind) {
    const contract = state.sceneApiContract;
    if (!contract) {
      notify("请先打开接口调用说明。", "warning");
      return;
    }
    const values = {
      endpoint: sceneApiEndpointUrl(contract),
      request: JSON.stringify(contract.request_example || {}, null, 2),
      response: JSON.stringify(contract.response_example || {}, null, 2),
    };
    try {
      await copyTextToClipboard(values[kind]);
      notify(kind === "endpoint" ? "接口地址已复制。" : "接口示例已复制。");
    } catch (error) {
      notify(error.message, "warning");
    }
  }

  function workflowPreflightLevelLabel(level) {
    return ({ ERROR: "需修复", WARNING: "需确认", INFO: "通过" })[String(level || "").toUpperCase()] || "提示";
  }

  function renderWorkflowPreflight(result) {
    const summary = result?.summary || {};
    const diagnostics = Array.isArray(result?.diagnostics) ? result.diagnostics : [];
    const ready = Boolean(result?.ready);
    const summaryElement = byId("workflowPreflightSummary");
    const stateLabel = ready ? "可发布" : "存在阻塞问题";
    summaryElement.innerHTML = `<span class="workflow-preflight-state ${ready ? "ready" : "blocked"}">${stateLabel}</span><p>错误 ${Number(summary.errors || 0)} 项 · 需确认 ${Number(summary.warnings || 0)} 项 · 已通过 ${Number(summary.infos || 0)} 项</p>`;
    const list = byId("workflowPreflightList");
    list.innerHTML = diagnostics.length
      ? diagnostics.map((item) => {
        const level = String(item.level || "INFO").toUpperCase();
        const target = item.node_name ? `<span class="workflow-preflight-node">${escapeHtml(item.node_name)} · ${escapeHtml(item.node_key || "")}</span>` : "";
        const suggestion = item.suggestion ? `<p>${escapeHtml(item.suggestion)}</p>` : "";
        return `<article class="workflow-preflight-item ${level.toLowerCase()}"><header><span>${workflowPreflightLevelLabel(level)}</span>${target}</header><strong>${escapeHtml(item.message || "未提供检查说明")}</strong>${suggestion}</article>`;
      }).join("")
      : '<div class="empty-state compact-empty"><strong>暂未返回检查项</strong><p>请重新执行编排检查。</p></div>';
  }

  async function runDesignerPreflight({ openModal = true, quiet = false } = {}) {
    const version = currentVersion();
    if (!version) return null;
    try {
      const result = await request(`${api}/scenarios/versions/${version.id}/preflight`);
      renderWorkflowPreflight(result);
      if (openModal) {
        const modalElement = byId("workflowPreflightModal");
        const modal = window.bootstrap?.Modal?.getOrCreateInstance(modalElement);
        if (modal) modal.show();
      }
      if (!quiet) notify(result.ready ? "编排检查通过，可以继续测试或发布。" : "编排检查发现需要处理的问题，请查看检查结果。", result.ready ? "success" : "warning");
      return result;
    } catch (error) {
      if (!quiet) notify(error.message, "danger");
      return null;
    }
  }

  function renderVlmOptions(id, selected, allowEmpty = true) {
    const options = state.vlms.filter((item) => item.enabled).map((model) => `<option value="${model.id}" ${Number(selected) === model.id ? "selected" : ""}>${escapeHtml(model.name)} · ${escapeHtml(model.model_name)}</option>`).join("");
    byId(id).innerHTML = `${allowEmpty ? '<option value="">不选择</option>' : '<option value="">请选择 VLM</option>'}${options}`;
  }

  function fitDirectDesignerToViewport() {
    const designer = byId("directDesigner");
    if (!window.matchMedia("(min-width: 761px)").matches || designer.hidden) {
      designer.style.removeProperty("--direct-designer-height");
      return;
    }
    const availableHeight = Math.max(420, Math.floor(window.innerHeight - designer.getBoundingClientRect().top));
    designer.style.setProperty("--direct-designer-height", `${availableHeight}px`);
  }

  function renderDirectDesigner() {
    const version = currentVersion();
    if (!version) return;
    const direct = state.scene.mode === "VLM_DIRECT";
    byId("directDesigner").hidden = !direct;
    byId("workflowDesigner").hidden = direct;
    if (!direct) { fitDirectDesignerToViewport(); return; }
    renderVlmOptions("directPrimaryVlm", version.primary_vlm_model_id, false);
    renderVlmOptions("directReviewVlm", version.review_vlm_model_id, true);
    byId("directPrompt").value = version.prompt_template || "";
    renderParameterRows("directInputParameters", schemaRows(version.input_schema_json || {}), {
      kind: "SCHEMA",
      readonly: !isDraft(),
      keyPlaceholder: "例如 ocr_text",
      valuePlaceholder: "例如 OCR 校验文字",
    });
    const parameters = version.definition_json?.vlm_parameters || {};
    byId("directTemperature").value = parameters.temperature ?? 0;
    byId("directMaxTokens").value = parameters.max_tokens ?? 1024;
    byId("directThinking").checked = Boolean(parameters.thinking_enabled);
    const extra = { ...parameters };
    delete extra.temperature;
    delete extra.max_tokens;
    delete extra.thinking_enabled;
    byId("directExtraParams").value = Object.keys(extra).length ? JSON.stringify(extra, null, 2) : "";
    ["directPrimaryVlm", "directReviewVlm", "directPrompt", "directTemperature", "directMaxTokens", "directThinking", "directExtraParams", "directSave", "directInputAdd"].forEach((id) => { byId(id).disabled = !isDraft(); });
    byId("directDraftTip").textContent = isDraft() ? "草稿可保存、测试与发布" : "已发布版本只读，但可直接测试";
    renderDirectPromptVariables(version);
    renderDirectTestInputs(version);
    requestAnimationFrame(fitDirectDesignerToViewport);
  }

  function renderDirectPromptVariables(version) {
    const container = byId("directPromptVariables");
    const fields = schemaFields(version?.input_schema_json || {})
      .filter((field) => {
        const name = String(field?.name || "").trim();
        return name && !imageInputNames.has(name.toLowerCase());
      });
    if (!fields.length) {
      container.innerHTML = '<small>未定义可传入参数；图片由系统自动传入。</small>';
      return;
    }
    const disabled = isDraft() ? "" : "disabled";
    container.innerHTML = `<small>点击插入参数到提示词：测试时会用右侧填写的值替换。</small><div>${fields.map((field) => {
      const name = String(field.name).trim();
      const label = String(field.label || name).trim();
      return `<button type="button" data-direct-prompt-variable="${escapeHtml(name)}" ${disabled} title="插入 {{ input.${escapeHtml(name)} }}">{{ input.${escapeHtml(name)} }} · ${escapeHtml(label)}</button>`;
    }).join("")}</div>`;
  }

  function insertDirectPromptVariable(name) {
    if (!isDraft()) return;
    const prompt = byId("directPrompt");
    const token = `{{ input.${name} }}`;
    const start = Number.isInteger(prompt.selectionStart) ? prompt.selectionStart : prompt.value.length;
    const end = Number.isInteger(prompt.selectionEnd) ? prompt.selectionEnd : start;
    prompt.value = `${prompt.value.slice(0, start)}${token}${prompt.value.slice(end)}`;
    const cursor = start + token.length;
    prompt.focus();
    prompt.setSelectionRange(cursor, cursor);
    prompt.dispatchEvent(new Event("input", { bubbles: true }));
  }

  function rememberDirectTestValues() {
    document.querySelectorAll("[data-direct-test-input]").forEach((input) => {
      const key = String(input.dataset.directTestInput || "").trim();
      if (key) state.directTestValues[key] = input.value;
    });
  }

  function directPromptInputReferences(prompt) {
    const references = new Set();
    const pattern = /\{\{\s*input\.([A-Za-z_][A-Za-z0-9_]*)\s*\}\}/g;
    let match;
    while ((match = pattern.exec(String(prompt || ""))) !== null) references.add(match[1]);
    return references;
  }

  function renderDirectTestInputs(version) {
    rememberDirectTestValues();
    const fields = schemaFields(version?.input_schema_json || {})
      .filter((field) => {
        const name = String(field?.name || "").trim();
        return name && !imageInputNames.has(name.toLowerCase());
      });
    const section = byId("directTestInputs");
    const container = byId("directTestInputFields");
    const hint = byId("directTestInputHint");
    if (!fields.length) {
      section.hidden = true;
      container.innerHTML = "";
      hint.textContent = "";
      return;
    }
    section.hidden = false;
    const referenced = directPromptInputReferences(version?.prompt_template || "");
    const notReferenced = fields
      .map((field) => String(field.name).trim())
      .filter((name) => !referenced.has(name));
    hint.textContent = notReferenced.length
      ? `提示词尚未引用：${notReferenced.join("、")}；填写后会传入场景，但不会影响 VLM 判断。`
      : "填写的值会替换提示词中的对应变量。";
    container.innerHTML = fields.map((field) => {
      const name = String(field.name).trim();
      const label = String(field.label || name).trim();
      const type = String(field.type || "TEXT").toUpperCase();
      const inputType = ["NUMBER", "INTEGER", "FLOAT"].includes(type) ? "number" : "text";
      const value = state.directTestValues[name] ?? field.default ?? "";
      const required = Boolean(field.required);
      return `<label><span title="${escapeHtml(name)}">${escapeHtml(label)}${required ? " <em>必填</em>" : ""}</span><input class="form-control form-control-sm" type="${inputType}" data-direct-test-input="${escapeHtml(name)}" value="${escapeHtml(value)}" placeholder="输入 ${escapeHtml(label)}"></label>`;
    }).join("");
  }

  function directTestContext() {
    const version = currentVersion();
    const fields = schemaFields(version?.input_schema_json || {})
      .filter((field) => {
        const name = String(field?.name || "").trim();
        return name && !imageInputNames.has(name.toLowerCase());
      });
    const values = {};
    for (const field of fields) {
      const name = String(field.name).trim();
      const input = document.querySelector(`[data-direct-test-input="${CSS.escape(name)}"]`);
      const value = String(input?.value ?? "").trim();
      if (field.required && !value) throw new Error(`请填写测试参数：${field.label || name}。`);
      if (value) values[name] = value;
      state.directTestValues[name] = value;
    }
    return values;
  }

  function nodeById(id) { return currentVersion()?.nodes.find((node) => node.id === Number(id)); }
  function nodeByKey(key) { return currentVersion()?.nodes.find((node) => node.node_key === key); }
  function defaultNodeName(type, nodeKey) {
    return `${nodeTypeLabel(type)}${nodeKey.match(/_(\d+)$/)?.[1] || ""}`;
  }
  function defaultNodeConfig(type) {
    if (type === "VLM") return { vlm_mode: "CUSTOM", prompt: "请检查图片并严格按 JSON 返回 result、confidence、reason。", context: {}, vlm_parameters: { temperature: 0, max_tokens: 1024 } };
    if (type === "VISION_MODEL") return { confidence: 0.25, iou: 0.45, context: {} };
    if (type === "IMAGE_CROP") return { image_path: "{{ input.image_path }}", bbox: "", objects: "", padding_ratio: 0.05, output_mapping: {} };
    if (type === "IMAGE_ANNOTATE") return { image_path: "{{ input.image_path }}", objects: "", bbox: "", draw_masks: true, show_label: true, show_confidence: true, line_width: 3, output_mapping: {} };
    if (type === "RULE") return { actual: "", operator: "EQUALS", expected: "OK" };
    if (type === "WEB_API") return { method: "POST", url: "", headers: {}, request_format: "JSON", body: {}, response_format: "JSON", context: {} };
    if (type === "S3_UPLOAD") return { image_path: "{{ input.image_path }}", key: "{{ input.result_s3_key }}", output_mapping: {} };
    if (type === "IF") return { actual: "", operator: "EQUALS", expected: "OK" };
    if (type === "LOOP") return { operation: "SUBFLOW", items: "", item_name: "item", max_iterations: 10, aggregate: "ALL_OK", body_nodes: [] };
    if (type === "ITERATION") return { items: "", item_name: "item", max_iterations: 10, aggregate: "ALL_OK", body_nodes: [] };
    if (type === "CONDITION_LOOP") return { initial_state: {}, state_updates: {}, stop_condition: { actual: "", operator: "EQUALS", expected: "" }, max_iterations: 3, timeout_seconds: 30, body_nodes: [] };
    return {};
  }
  function nodeKeyFor(type) {
    const prefix = ({ VLM: "vlm", VISION_MODEL: "model", IMAGE_CROP: "crop", IMAGE_ANNOTATE: "annotate", RULE: "rule", WEB_API: "api", S3_UPLOAD: "s3", IF: "if", LOOP: "loop", ITERATION: "iteration", CONDITION_LOOP: "condition_loop" }[type] || "node");
    let index = 1;
    const keys = new Set([...(currentVersion()?.nodes || []).map((node) => node.node_key), ...state.pendingNodeKeys]);
    while (keys.has(`${prefix}_${index}`)) index += 1;
    return `${prefix}_${index}`;
  }
  function canvasPoint(event) {
    const viewport = byId("workflowCanvasViewport");
    const rect = viewport.getBoundingClientRect();
    return {
      x: Math.max(20, Math.min(canvasWidth - nodeWidth - 20, (event.clientX - rect.left + viewport.scrollLeft) / state.zoom - nodeWidth / 2)),
      y: Math.max(20, Math.min(canvasHeight - nodeHeight - 20, (event.clientY - rect.top + viewport.scrollTop) / state.zoom - nodeHeight / 2)),
    };
  }
  function nodePosition(node) { return { x: Number(node.canvas_x || 0), y: Number(node.canvas_y || 0) }; }
  function edgePath(source, target, layout) {
    const start = layout.positions.get(source.id); const end = layout.positions.get(target.id);
    const sourceWidth = source.id === layout.expandedNodeId ? layout.container.width : nodeWidth;
    const x1 = start.x + sourceWidth; const y1 = start.y + nodeHeight / 2;
    const x2 = end.x; const y2 = end.y + nodeHeight / 2;
    const offset = Math.max(90, Math.abs(x2 - x1) * 0.45);
    return `M ${x1} ${y1} C ${x1 + offset} ${y1}, ${x2 - offset} ${y2}, ${x2} ${y2}`;
  }
  function workflowDisplayLayout(nodes) {
    const positions = new Map(nodes.map((node) => [node.id, nodePosition(node)]));
    const parent = nodes.find((node) => node.id === state.subflowNodeId);
    if (!parent) return { positions, subflow: null, container: null, expandedNodeId: null, width: canvasWidth, height: canvasHeight };
    const parentPosition = positions.get(parent.id);
    const viewportWidth = byId("workflowCanvasViewport")?.clientWidth || subflowWidth * state.zoom + 32;
    const containerWidth = Math.min(subflowWidth, Math.max(850, Math.floor((viewportWidth - 32) / state.zoom)));
    const container = { x: parentPosition.x, y: parentPosition.y, width: containerWidth, height: nodeHeight + subflowHeight + 12 };
    const subflow = { x: container.x + 12, y: container.y + nodeHeight, width: container.width - 24, height: subflowHeight };
    for (const node of nodes) {
      if (node.id === parent.id) continue;
      const position = positions.get(node.id);
      if (position.y + nodeHeight <= container.y || position.y >= container.y + container.height + 48) continue;
      if (position.x >= parentPosition.x + nodeWidth / 2) {
        positions.set(node.id, { x: position.x + container.width - nodeWidth / 2 + 48, y: position.y });
      } else if (position.y >= parentPosition.y + nodeHeight / 2 && position.x + nodeWidth > container.x) {
        positions.set(node.id, { x: position.x, y: position.y + container.height - nodeHeight / 2 + 48 });
      }
    }
    return {
      positions,
      subflow,
      container,
      expandedNodeId: parent.id,
      width: Math.max(canvasWidth, container.x + container.width + 48, ...[...positions.values()].map((position) => position.x + nodeWidth + 48)),
      height: Math.max(canvasHeight, container.y + container.height + 48, ...[...positions.values()].map((position) => position.y + nodeHeight + 48)),
    };
  }
  function fitWorkflowToViewport(nodes) {
    const version = currentVersion();
    const viewport = byId("workflowCanvasViewport");
    if (!version || !viewport || state.fittedWorkflowVersions.has(version.id) || !nodes.length) return;
    const maxRight = Math.max(...nodes.map((node) => nodePosition(node).x + nodeWidth));
    const minLeft = Math.min(...nodes.map((node) => nodePosition(node).x));
    const maxBottom = Math.max(...nodes.map((node) => nodePosition(node).y + nodeHeight));
    const minTop = Math.min(...nodes.map((node) => nodePosition(node).y));
    const graphWidth = Math.max(nodeWidth, maxRight - minLeft) + 72;
    const graphHeight = Math.max(nodeHeight, maxBottom - minTop) + 72;
    const availableWidth = Math.max(260, viewport.clientWidth - 44);
    const availableHeight = Math.max(200, viewport.clientHeight - 44);
    state.zoom = Math.max(0.5, Math.min(1, availableWidth / graphWidth, availableHeight / graphHeight));
    state.fittedWorkflowVersions.add(version.id);
  }

  function centerWorkflowViewport(nodes) {
    const viewport = byId("workflowCanvasViewport");
    if (!viewport || !nodes.length) return;
    const positions = nodes.map(nodePosition);
    const minLeft = Math.min(...positions.map((position) => position.x));
    const maxRight = Math.max(...positions.map((position) => position.x + nodeWidth));
    const minTop = Math.min(...positions.map((position) => position.y));
    const maxBottom = Math.max(...positions.map((position) => position.y + nodeHeight));
    const centerX = (minLeft + maxRight) / 2;
    const centerY = (minTop + maxBottom) / 2;
    viewport.scrollLeft = Math.max(0, centerX * state.zoom - viewport.clientWidth / 2);
    viewport.scrollTop = Math.max(0, centerY * state.zoom - viewport.clientHeight / 2);
  }

  function focusWorkflowNode(nodeId) {
    closeSubflowCanvas();
    const node = nodeById(nodeId);
    if (!node || !node.enabled) return;
    state.selectedNodeId = node.id;
    state.selectedEdgeId = null;
    openInspectorForNode();
    renderWorkflowCanvas();
    renderInspector();
    requestAnimationFrame(() => {
      const viewport = byId("workflowCanvasViewport");
      const viewportRect = viewport.getBoundingClientRect();
      const workbenchRect = byId("workflowDesigner").getBoundingClientRect();
      const palette = byId("workflowPalette");
      const paletteRight = palette ? workbenchRect.left + palette.offsetLeft + palette.offsetWidth - viewportRect.left : 0;
      if (!state.paletteCollapsed && palette && nodePosition(node).x * state.zoom < paletteRight + 16) {
        setWorkbenchPanel("palette", true);
      }
      const inspector = byId("workflowInspector");
      const visibleRight = state.inspectorCollapsed || !inspector
        ? viewport.clientWidth - 16
        : Math.max(16, workbenchRect.left + inspector.offsetLeft - viewportRect.left - 16);
      const visibleLeft = state.paletteCollapsed || !palette
        ? 16
        : Math.max(16, paletteRight + 16);
      const centerX = (visibleLeft + visibleRight) / 2;
      const position = nodePosition(node);
      viewport.scrollLeft = Math.max(0, (position.x + nodeWidth / 2) * state.zoom - centerX);
      viewport.scrollTop = Math.max(0, (position.y + nodeHeight / 2) * state.zoom - viewport.clientHeight / 2);
    });
  }

  function fitWorkflowCanvas() {
    const version = currentVersion();
    const nodes = (version?.nodes || []).filter((node) => node.enabled);
    if (!version || !nodes.length) return;
    state.fittedWorkflowVersions.delete(version.id);
    fitWorkflowToViewport(nodes);
    renderWorkflowCanvas();
    requestAnimationFrame(() => centerWorkflowViewport(nodes));
  }

  function workflowLayoutPositions(nodes, edges) {
    const nodesByKey = new Map(nodes.map((node) => [node.node_key, node]));
    const incoming = new Map(nodes.map((node) => [node.node_key, []]));
    edges.forEach((edge) => {
      if (nodesByKey.has(edge.source_node_key) && nodesByKey.has(edge.target_node_key)) {
        incoming.get(edge.target_node_key).push(edge.source_node_key);
      }
    });
    const levels = new Map();
    const resolveLevel = (nodeKey, visiting = new Set()) => {
      if (levels.has(nodeKey)) return levels.get(nodeKey);
      const node = nodesByKey.get(nodeKey);
      if (!node || node.node_type === "START") { levels.set(nodeKey, 0); return 0; }
      if (visiting.has(nodeKey)) return 0;
      const nextVisiting = new Set(visiting);
      nextVisiting.add(nodeKey);
      const parents = incoming.get(nodeKey) || [];
      const level = parents.length ? Math.max(...parents.map((parent) => resolveLevel(parent, nextVisiting))) + 1 : 1;
      levels.set(nodeKey, level);
      return level;
    };
    nodes.forEach((node) => resolveLevel(node.node_key));
    const groups = new Map();
    nodes.forEach((node) => {
      const level = levels.get(node.node_key) || 0;
      if (!groups.has(level)) groups.set(level, []);
      groups.get(level).push(node);
    });
    const positions = new Map();
    [...groups.entries()].sort(([left], [right]) => left - right).forEach(([level, group]) => {
      group.sort((left, right) => {
        const systemOrder = (node) => node.node_type === "START" ? -1 : node.node_type === "END" ? 1 : 0;
        return systemOrder(left) - systemOrder(right) || nodePosition(left).y - nodePosition(right).y || left.id - right.id;
      });
      group.forEach((node, index) => {
        positions.set(node.id, {
          x: Math.min(canvasWidth - nodeWidth - 44, 96 + level * 314),
          y: Math.min(canvasHeight - nodeHeight - 44, 126 + index * 164),
        });
      });
    });
    return positions;
  }

  async function autoLayoutWorkflow() {
    if (!isDraft()) { notify("已发布版本不能修改，请先新建草稿版本。", "warning"); return; }
    const version = currentVersion();
    const nodes = (version?.nodes || []).filter((node) => node.enabled);
    if (nodes.length <= 2) { notify("请先从节点库拖入至少一个执行节点。", "info"); return; }
    const edges = (version.edges || []).filter((edge) => nodeByKey(edge.source_node_key) && nodeByKey(edge.target_node_key));
    const positions = workflowLayoutPositions(nodes, edges);
    try {
      await Promise.all(nodes.map((node) => {
        const position = positions.get(node.id);
        node.canvas_x = position.x;
        node.canvas_y = position.y;
        return request(`${api}/scenarios/versions/${version.id}/nodes/${node.id}`, {
          method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ canvas_x: position.x, canvas_y: position.y }),
        });
      }));
      state.fittedWorkflowVersions.add(version.id);
      renderWorkflowCanvas();
      requestAnimationFrame(() => centerWorkflowViewport(nodes));
      notify("已按执行路径自动排版，可继续微调节点位置。");
    } catch (error) { notify(error.message, "danger"); }
  }

  function filterWorkflowPalette(query) {
    const normalized = String(query || "").trim().toLowerCase();
    const paletteNodes = [...document.querySelectorAll(".palette-node")];
    let visibleCount = 0;
    paletteNodes.forEach((node) => {
      const match = !normalized || String(node.dataset.nodeKeywords || node.textContent).toLowerCase().includes(normalized);
      node.hidden = !match;
      if (match) visibleCount += 1;
    });
    document.querySelectorAll("[data-palette-group]").forEach((group) => {
      group.hidden = ![...group.querySelectorAll(".palette-node")].some((node) => !node.hidden);
    });
    byId("workflowPaletteEmpty").hidden = visibleCount > 0;
    byId("workflowPaletteCount").textContent = String(visibleCount);
  }

  function renderWorkflowCanvas() {
    const version = currentVersion();
    if (!version) return;
    const surface = byId("workflowCanvasSurface");
    const nodeLayer = byId("workflowNodeLayer");
    const nodes = [...(version.nodes || [])].filter((node) => node.enabled).sort((a, b) => a.sort_order - b.sort_order || a.id - b.id);
    const layout = workflowDisplayLayout(nodes);
    surface.style.width = `${layout.width}px`;
    surface.style.height = `${layout.height}px`;
    surface.style.transform = `scale(${state.zoom})`;
    surface.style.transformOrigin = "0 0";
    const navigator = byId("workflowNodeNavigator");
    if (navigator) {
      navigator.innerHTML = `<option value="">定位节点（${nodes.length}）</option>${nodes.map((node) => {
        const incoming = node.node_type === "START" || (version.edges || []).some((edge) => edge.target_node_key === node.node_key);
        const outgoing = node.node_type === "END" || (version.edges || []).some((edge) => edge.source_node_key === node.node_key);
        const connection = incoming && outgoing ? "" : " · 未连通";
        return `<option value="${node.id}">${escapeHtml(nodeTypeLabel(node.node_type))} · ${escapeHtml(node.name)} (${escapeHtml(node.node_key)})${connection}</option>`;
      }).join("")}`;
      navigator.value = nodes.some((node) => node.id === state.selectedNodeId) ? String(state.selectedNodeId) : "";
    }
    fitWorkflowToViewport(nodes);
    surface.style.transform = `scale(${state.zoom})`;
    nodeLayer.innerHTML = nodes.map((node) => {
      const position = layout.positions.get(node.id);
      const systemNode = ["START", "END"].includes(node.node_type);
      const selected = node.id === state.selectedNodeId;
      const expanded = node.id === layout.expandedNodeId;
      const contract = workflowNodeContract(node);
      const inputLabel = contract.inputs[0]?.label || "无额外输入";
      const outputLabel = contract.outputs[0]?.label || "无输出";
      return `<article class="workflow-canvas-node workflow-node-${String(node.node_type).toLowerCase()} ${selected ? "selected" : ""} ${systemNode ? "system-node" : ""} ${expanded ? "subflow-expanded" : ""}" data-node-id="${node.id}" style="left:${position.x}px;top:${position.y}px;${expanded ? `width:${layout.container.width}px;height:${layout.container.height}px;` : ""}">
        <button class="workflow-port workflow-input-port" data-input-node="${escapeHtml(node.node_key)}" type="button" title="输入端点"></button>
        <div class="workflow-node-content"><div class="workflow-node-label"><span class="workflow-node-icon" aria-hidden="true">${nodeTypeIcon(node.node_type)}</span><small>${escapeHtml(nodeTypeLabel(node.node_type))}</small></div><strong>${escapeHtml(node.name)}</strong><span class="workflow-node-key">${escapeHtml(node.node_key)}</span><div class="workflow-node-contract" title="输入：${escapeHtml(inputLabel)}；输出：${escapeHtml(outputLabel)}"><span>IN · ${escapeHtml(inputLabel)}</span><span>OUT · ${escapeHtml(outputLabel)}</span></div></div>
        ${loopTypes.has(node.node_type) && (node.node_type !== "LOOP" || node.config_json?.operation === "SUBFLOW") ? `<button type="button" class="workflow-subflow-open" data-subflow-open="${node.id}" aria-expanded="${state.subflowNodeId === node.id}" title="${state.subflowNodeId === node.id ? "收起" : "展开"}循环体">${state.subflowNodeId === node.id ? "收起循环体 ↑" : "展开循环体 ↓"}</button>` : ""}
        <button class="workflow-port workflow-output-port" data-output-node="${escapeHtml(node.node_key)}" type="button" title="输出端点"></button>
      </article>`;
    }).join("");
    const edges = (version.edges || []).filter((edge) => {
      const source = nodeByKey(edge.source_node_key);
      const target = nodeByKey(edge.target_node_key);
      return source && target && layout.positions.has(source.id) && layout.positions.has(target.id);
    });
    if (!edges.some((edge) => edge.id === state.selectedEdgeId)) state.selectedEdgeId = null;
    const edgeLayer = byId("workflowEdgeLayer");
    edgeLayer.setAttribute("viewBox", `0 0 ${layout.width} ${layout.height}`);
    edgeLayer.setAttribute("width", String(layout.width));
    edgeLayer.setAttribute("height", String(layout.height));
    edgeLayer.innerHTML = `<defs><marker id="workflowArrow" markerWidth="10" markerHeight="10" refX="8" refY="3" orient="auto" markerUnits="strokeWidth"><path d="M0,0 L0,6 L9,3 z"></path></marker></defs>${edges.map((edge) => {
      const source = nodeByKey(edge.source_node_key); const target = nodeByKey(edge.target_node_key);
      const branch = edge.mapping_json?.branch;
      const path = edgePath(source, target, layout);
      const sourcePosition = layout.positions.get(source.id);
      const targetPosition = layout.positions.get(target.id);
      const sourceWidth = source.id === layout.expandedNodeId ? layout.container.width : nodeWidth;
      return `<g class="workflow-edge-group ${edge.id === state.selectedEdgeId ? "selected" : ""}" data-edge-id="${edge.id}"><path class="workflow-edge-hit" d="${path}"></path><path class="workflow-edge" d="${path}" marker-end="url(#workflowArrow)"></path>${branch ? `<text class="workflow-edge-label" x="${(sourcePosition.x + targetPosition.x + sourceWidth) / 2}" y="${(sourcePosition.y + targetPosition.y + nodeHeight) / 2 - 9}">${escapeHtml(branch)}</text>` : ""}</g>`;
    }).join("")}`;
    const subflowPanel = byId("workflowSubflowPanel");
    if (layout.subflow && subflowPanel) {
      subflowPanel.style.left = `${layout.subflow.x}px`;
      subflowPanel.style.top = `${layout.subflow.y}px`;
      subflowPanel.style.width = `${layout.subflow.width}px`;
      subflowPanel.style.height = `${layout.subflow.height}px`;
    }
    const deleteEdgeButton = byId("workflowDeleteEdge");
    const selectedEdge = edges.find((edge) => edge.id === state.selectedEdgeId);
    deleteEdgeButton.disabled = !isDraft() || !selectedEdge;
    deleteEdgeButton.title = selectedEdge
      ? `删除 ${nodeByKey(selectedEdge.source_node_key)?.name || selectedEdge.source_node_key} → ${nodeByKey(selectedEdge.target_node_key)?.name || selectedEdge.target_node_key}`
      : "先点击画布中的连线";
    byId("workflowCanvasEmpty").hidden = nodes.length > 2;
    const workflowHint = byId("workflowCanvasHint");
    if (workflowHint) workflowHint.textContent = state.subflowNodeId !== null
      ? "循环体已在当前画布展开；收起后可拖动主流程节点。"
      : isDraft() ? "拖拽节点、拖动端点连线；发布前会校验有效的“开始 → 结束”路径。" : "已发布版本只读；请新建草稿版本后修改画布。";
    byId("workflowZoomReset").textContent = `${Math.round(state.zoom * 100)}%`;
    const graphSummary = byId("workflowGraphSummary");
    if (graphSummary) graphSummary.textContent = `${nodes.length} 个节点 · ${edges.length} 条连线 · ${isDraft() ? "草稿可编辑" : "已发布只读"}`;
  }

  const fallbackVisionOutputProfile = Object.freeze({
    task_type: "UNKNOWN",
    display_name: "通用模型",
    summary: "仅声明通用结果、置信度和说明；未声明定位框能力。",
    supports_objects: false,
    supports_bbox: false,
    supports_mask: false,
    supports_classification: false,
    output_keys: ["result", "confidence", "reason", "task_type"],
  });
  const visionOutputProfiles = Object.freeze({
    YOLO_DETECTION: {
      task_type: "YOLO_DETECTION", display_name: "YOLO 目标检测", summary: "输出目标列表、类别、置信度、数量和 bbox。",
      supports_objects: true, supports_bbox: true, supports_mask: false, supports_classification: false,
      output_keys: ["confidence", "task_type", "image", "image.width", "image.height", "detection_count", "object_count", "objects", "objects.0.class_id", "objects.0.label", "objects.0.confidence", "objects.0.bbox", "objects.0.bbox_format"],
    },
    YOLO_SEGMENTATION: {
      task_type: "YOLO_SEGMENTATION", display_name: "YOLO 目标分割", summary: "当前 YOLO 分割适配器输出目标列表、bbox 和 mask。",
      supports_objects: true, supports_bbox: true, supports_mask: true, supports_classification: false,
      output_keys: ["confidence", "task_type", "image", "image.width", "image.height", "detection_count", "object_count", "objects", "objects.0.class_id", "objects.0.label", "objects.0.confidence", "objects.0.bbox", "objects.0.bbox_format", "objects.0.mask", "objects.0.mask_format"],
    },
    YOLO_CLASSIFICATION: {
      task_type: "YOLO_CLASSIFICATION", display_name: "YOLO 图像分类", summary: "输出 Top1 / Top5 类别和置信度，不输出 bbox。",
      supports_objects: false, supports_bbox: false, supports_mask: false, supports_classification: true,
      output_keys: ["predicted_label", "confidence", "task_type", "image", "image.width", "image.height", "classification", "classification.top1_class_id", "classification.top1_label", "classification.top1_confidence", "classification.top5"],
    },
  });

  const workflowDataTypeLabels = Object.freeze({
    ANY: "任意",
    TEXT: "文本",
    NUMBER: "数字",
    BOOLEAN: "布尔值",
    JSON: "JSON",
    IMAGE: "图片",
    IMAGE_LIST: "图片列表",
    BBOX: "定位框",
    OBJECT_LIST: "目标列表",
    MASK: "轮廓",
    MASK_LIST: "轮廓列表",
    RESULT: "判定结果",
  });

  function normalizeWorkflowDataType(value) {
    const normalized = String(value || "ANY").trim().toUpperCase();
    if (["STRING", "STR", "INTEGER", "FLOAT", "DECIMAL"].includes(normalized)) {
      return ["INTEGER", "FLOAT", "DECIMAL"].includes(normalized) ? "NUMBER" : "TEXT";
    }
    return workflowDataTypeLabels[normalized] ? normalized : "ANY";
  }

  function workflowTypeLabel(value) {
    const normalized = normalizeWorkflowDataType(value);
    return workflowDataTypeLabels[normalized] || workflowDataTypeLabels.ANY;
  }

  function workflowTypeClass(value) {
    return normalizeWorkflowDataType(value).toLowerCase().replace(/[^a-z0-9_-]/g, "-");
  }

  function contractDescriptor(name, type = "ANY", label = "") {
    return { name, type: normalizeWorkflowDataType(type), label: label || name };
  }

  function visionOutputProfile(modelOrTask) {
    const remote = modelOrTask && typeof modelOrTask === "object" ? modelOrTask.output_profile : null;
    const taskType = String(
      typeof modelOrTask === "string" ? modelOrTask : modelOrTask?.task_type || remote?.task_type || "",
    ).toUpperCase();
    const local = visionOutputProfiles[taskType] || fallbackVisionOutputProfile;
    const profile = remote && typeof remote === "object" ? { ...local, ...remote } : local;
    return {
      ...fallbackVisionOutputProfile,
      ...profile,
      output_keys: Array.isArray(profile.output_keys) ? profile.output_keys : fallbackVisionOutputProfile.output_keys,
    };
  }

  function visionOutputKeysForNode(node) {
    const selected = selectedVisionModelVersion(node?.config_json?.model_version_id);
    return visionOutputProfile(selected).output_keys;
  }

  function nodeOutputDescriptors(node) {
    const nodeType = String(node?.node_type || "").toUpperCase();
    const profile = nodeType === "VISION_MODEL"
      ? visionOutputProfile(selectedVisionModelVersion(node?.config_json?.model_version_id))
      : null;
    const descriptors = {
      START: [
        contractDescriptor("image_path", "IMAGE", "检测图片（配方调用时为 ROI 图）"),
        contractDescriptor("original_image_path", "IMAGE", "原始整图"),
        contractDescriptor("source_image_path", "TEXT", "原图来源路径"),
        contractDescriptor("roi_bbox", "BBOX", "ROI 在原图中的位置（配方调用时可用）"),
        contractDescriptor("result_s3_key", "TEXT", "外部接口指定的上传对象键（可选）"),
      ],
      VLM: [
        contractDescriptor("result", "RESULT", "判定结果"),
        contractDescriptor("confidence", "NUMBER", "置信度"),
        contractDescriptor("reason", "TEXT", "检测说明"),
      ],
      VISION_MODEL: profile?.supports_classification ? [
        contractDescriptor("predicted_label", "TEXT", "预测类别"),
        contractDescriptor("confidence", "NUMBER", "分类置信度"),
        contractDescriptor("classification", "JSON", "Top1 / Top5 分类结果"),
        contractDescriptor("task_type", "TEXT", "模型任务类型"),
        contractDescriptor("image", "JSON", "图片尺寸信息"),
      ] : profile?.supports_objects ? [
        contractDescriptor("confidence", "NUMBER", "最高目标置信度"),
        contractDescriptor("task_type", "TEXT", "模型任务类型"),
        contractDescriptor("image", "JSON", "图片尺寸信息"),
      ] : [
        contractDescriptor("result", "RESULT", "判定结果"),
        contractDescriptor("confidence", "NUMBER", "置信度"),
        contractDescriptor("reason", "TEXT", "检测说明"),
        contractDescriptor("task_type", "TEXT", "模型任务类型"),
        contractDescriptor("image", "JSON", "图片尺寸信息"),
      ],
      IMAGE_CROP: [
        contractDescriptor("result", "RESULT", "裁剪状态"),
        contractDescriptor("image_path", "IMAGE", "裁剪图片"),
        contractDescriptor("image_url", "TEXT", "裁剪图片地址"),
        contractDescriptor("crop_bbox", "BBOX", "实际裁剪框"),
        contractDescriptor("width", "NUMBER", "裁剪宽度"),
        contractDescriptor("height", "NUMBER", "裁剪高度"),
        contractDescriptor("crops", "IMAGE_LIST", "批量裁剪图片"),
        contractDescriptor("crops.0.image_path", "IMAGE", "首张裁剪图"),
        contractDescriptor("count", "NUMBER", "裁剪数量"),
      ],
      IMAGE_ANNOTATE: [
        contractDescriptor("result", "RESULT", "画框状态"),
        contractDescriptor("image_path", "IMAGE", "标注结果图"),
        contractDescriptor("image_url", "TEXT", "标注图片地址"),
        contractDescriptor("annotations", "OBJECT_LIST", "已标注目标"),
        contractDescriptor("annotation_count", "NUMBER", "标注数量"),
      ],
      RULE: [
        contractDescriptor("result", "RESULT", "规则结果"),
        contractDescriptor("actual", "ANY", "实际值"),
        contractDescriptor("expected", "ANY", "期望值"),
        contractDescriptor("operator", "TEXT", "比较运算符"),
      ],
      IF: [
        contractDescriptor("result", "RESULT", "判断结果"),
        contractDescriptor("branch", "BOOLEAN", "分支条件"),
        contractDescriptor("actual", "ANY", "判断值"),
        contractDescriptor("expected", "ANY", "期望值"),
      ],
      WEB_API: [
        contractDescriptor("result", "RESULT", "接口调用结果"),
        contractDescriptor("confidence", "NUMBER", "置信度"),
        contractDescriptor("status_code", "NUMBER", "HTTP 状态码"),
        contractDescriptor("response", "JSON", "接口响应"),
      ],
      S3_UPLOAD: [
        contractDescriptor("s3_uri", "TEXT", "S3 对象路径"),
        contractDescriptor("result_image_path", "TEXT", "上传后的结果图路径"),
        contractDescriptor("status", "TEXT", "上传状态"),
      ],
      LOOP: [
        contractDescriptor("result", "RESULT", "循环体逐项汇总结果"),
        contractDescriptor("items", "JSON", "每项检测结果"),
        contractDescriptor("items.0.result", "RESULT", "首项目标结果"),
        contractDescriptor("count", "NUMBER", "处理数量"),
      ],
      ITERATION: [
        contractDescriptor("result", "RESULT", "逐项汇总结果"),
        contractDescriptor("items", "JSON", "每项执行结果与追踪"),
        contractDescriptor("items.0.output", "JSON", "首项子流程输出"),
        contractDescriptor("count", "NUMBER", "实际遍历数量"),
      ],
      CONDITION_LOOP: [
        contractDescriptor("result", "RESULT", "条件循环最终结果"),
        contractDescriptor("state", "JSON", "最终循环变量"),
        contractDescriptor("rounds", "JSON", "每轮执行结果与追踪"),
        contractDescriptor("count", "NUMBER", "实际执行轮数"),
        contractDescriptor("exit_reason", "TEXT", "退出原因"),
      ],
      END: [
        contractDescriptor("result", "RESULT", "最终结果"),
        contractDescriptor("confidence", "NUMBER", "最终置信度"),
        contractDescriptor("reason", "TEXT", "最终说明"),
      ],
    }[nodeType] || [contractDescriptor("result", "RESULT", "执行结果")];

    if (nodeType === "VISION_MODEL" && profile) {
      if (profile.supports_objects) {
        descriptors.push(
          contractDescriptor("objects", "OBJECT_LIST", "目标列表"),
          contractDescriptor("object_count", "NUMBER", "目标数量"),
          contractDescriptor("detection_count", "NUMBER", "检测数量"),
          contractDescriptor("objects.0.bbox", "BBOX", "首个目标定位框"),
        );
      }
      if (profile.supports_mask) descriptors.push(contractDescriptor("objects.0.mask", "MASK", "首个目标轮廓"));
      if (profile.supports_classification) {
        descriptors.push(
          contractDescriptor("classification.top1_label", "TEXT", "Top1 类别"),
          contractDescriptor("classification.top1_confidence", "NUMBER", "Top1 置信度"),
        );
      }
    }

    Object.entries((node?.config_json || {}).output_mapping || {}).forEach(([name, expression]) => {
      if (!descriptors.some((item) => item.name === name)) {
        const sourceName = /^\{\{\s*response\.([\w.]+)\s*\}\}$/.exec(String(expression || ""))?.[1];
        const source = descriptors.find((item) => item.name === sourceName);
        descriptors.push(contractDescriptor(name, source?.type || "ANY", source ? `${source.label}（${name}）` : `自定义输出 · ${name}`));
      }
    });
    return descriptors;
  }

  function workflowNodeInputDescriptors(node) {
    const nodeType = String(node?.node_type || "").toUpperCase();
    const config = node?.config_json || {};
    const inputs = {
      START: [
        contractDescriptor("image_path", "IMAGE", "检测图片（系统输入）"),
        contractDescriptor("original_image_path", "IMAGE", "原始整图（系统输入）"),
        contractDescriptor("source_image_path", "TEXT", "原图来源路径（系统输入）"),
        contractDescriptor("roi_bbox", "BBOX", "ROI 在原图中的位置（配方调用时可用）"),
        contractDescriptor("result_s3_key", "TEXT", "外部接口指定的上传对象键（可选）"),
        ...workflowInputFields().filter((field) => !imageInputNames.has(String(field.name || "").toLowerCase())).map((field) => contractDescriptor(field.name, field.type || "TEXT", field.label || field.name)),
      ],
      VLM: [
        contractDescriptor("image_path", "IMAGE", "待检测图片"),
        ...Object.keys(config.input_mapping ?? config.context ?? {}).map((name) => contractDescriptor(name, "ANY", `提示词参数 · ${name}`)),
      ],
      VISION_MODEL: [
        contractDescriptor("image_path", "IMAGE", "待检测图片"),
        ...Object.keys(config.input_mapping ?? config.context ?? {}).map((name) => contractDescriptor(name, "ANY", `模型参数 · ${name}`)),
      ],
      IMAGE_CROP: [contractDescriptor("image_path", "IMAGE", "输入图片"), contractDescriptor("bbox", "BBOX", "单个定位框"), contractDescriptor("objects", "OBJECT_LIST", "目标列表（批量裁剪）")],
      IMAGE_ANNOTATE: [contractDescriptor("image_path", "IMAGE", "输入图片"), contractDescriptor("objects", "OBJECT_LIST", "目标列表"), contractDescriptor("bbox", "BBOX", "单个定位框")],
      RULE: [contractDescriptor("actual", "ANY", "实际值"), contractDescriptor("expected", "ANY", "期望值")],
      IF: [contractDescriptor("actual", "ANY", "判断值"), contractDescriptor("expected", "ANY", "期望值")],
      WEB_API: [contractDescriptor("url", "TEXT", "接口地址"), contractDescriptor("body", "JSON", "请求体")],
      S3_UPLOAD: [contractDescriptor("image_path", "IMAGE", "待上传图片"), contractDescriptor("key", "TEXT", "桶内相对对象键")],
      LOOP: [contractDescriptor("items", "JSON", "待逐项处理的数组")],
      ITERATION: [contractDescriptor("items", "JSON", "待逐项处理的数组")],
      CONDITION_LOOP: [contractDescriptor("initial_state", "JSON", "循环初始变量")],
      END: Object.keys(config.output || {}).map((name) => contractDescriptor(name, "ANY", `返回字段 · ${name}`)),
    }[nodeType] || [];
    return inputs;
  }

  function workflowNodeContract(node) {
    return {
      inputs: workflowNodeInputDescriptors(node),
      outputs: nodeOutputDescriptors(node),
    };
  }

  function contractRowsMarkup(items, emptyText) {
    if (!items.length) return `<p class="node-contract-empty">${escapeHtml(emptyText)}</p>`;
    return `<div class="node-contract-list">${items.map((item) => `<div><code>${escapeHtml(item.name)}</code><span>${escapeHtml(item.label)}</span><em class="workflow-data-type ${workflowTypeClass(item.type)}">${escapeHtml(workflowTypeLabel(item.type))}</em></div>`).join("")}</div>`;
  }

  function workflowNodeContractMarkup(node) {
    const contract = workflowNodeContract(node);
    return `<div class="node-contract-overview"><p>字段类型用于变量选择与发布前校验。只有已连通上游节点的兼容字段会出现在变量选择器中。</p><section><header><strong>输入契约</strong><small>节点需要的数据</small></header>${contractRowsMarkup(contract.inputs, "本节点不需要额外输入字段。")}</section><section><header><strong>输出契约</strong><small>可供下游引用的数据</small></header>${contractRowsMarkup(contract.outputs, "本节点没有对外输出字段。")}</section></div>`;
  }

  function nodeOutputKeys(node) {
    return [...new Set(nodeOutputDescriptors(node).map((item) => item.name))];
  }

  function upstreamNodeKeys(node) {
    if (!node || state.scene?.mode !== "WORKFLOW") return new Set();
    const edges = currentVersion()?.edges || [];
    const upstream = new Set();
    const pending = edges
      .filter((edge) => edge.target_node_key === node.node_key)
      .map((edge) => edge.source_node_key);
    while (pending.length) {
      const nodeKey = pending.shift();
      if (!nodeKey || upstream.has(nodeKey)) continue;
      upstream.add(nodeKey);
      edges
        .filter((edge) => edge.target_node_key === nodeKey)
        .forEach((edge) => pending.push(edge.source_node_key));
    }
    return upstream;
  }

  function variableScopeForTarget(target) {
    return String(target?.dataset?.variableScope || target?.closest?.("[data-variable-scope]")?.dataset?.variableScope || "INPUT").toUpperCase();
  }

  function outputReferenceItems(node) {
    const descriptors = nodeOutputDescriptors(node);
    return [
      ...descriptors.map((item) => ({ label: item.label, token: `{{ response.${item.name} }}`, type: item.type })),
      { label: "完整原始输出", token: "{{ response }}", type: "JSON" },
    ];
  }

  function promptMappedParameterKeys(node) {
    const config = node?.config_json || {};
    const keys = new Set(Object.keys(config.input_mapping ?? config.context ?? {}));
    const activeRows = node?.id === state.selectedNodeId ? byId("nodeInputParameters") : null;
    activeRows?.querySelectorAll("[data-parameter-row]").forEach((row) => {
      const key = row.querySelector("[data-parameter-key]")?.value.trim() || "";
      if (parameterKeyPattern.test(key)) keys.add(key);
    });
    return [...keys].sort((left, right) => left.localeCompare(right));
  }

  function variableGroups(node, scope = "INPUT") {
    const normalizedScope = String(scope).toUpperCase();
    if (normalizedScope === "OUTPUT") {
      return [{ label: "当前节点原始输出", description: "为当前节点输出设置易读别名", items: outputReferenceItems(node) }];
    }
    const fields = state.scene?.mode === "WORKFLOW"
      ? workflowInputFields()
      : schemaFields(currentVersion()?.input_schema_json || {});
    const inputItems = [
      { label: "检测图片（ROI）", token: "{{ input.image_path }}", type: "IMAGE" },
      { label: "原始整图", token: "{{ input.original_image_path }}", type: "IMAGE" },
      { label: "原图来源路径", token: "{{ input.source_image_path }}", type: "TEXT" },
      { label: "ROI 原图坐标（配方调用）", token: "{{ input.roi_bbox }}", type: "BBOX" },
      { label: "外部接口结果图对象键（可选）", token: "{{ input.result_s3_key }}", type: "TEXT" },
      ...fields
        .filter((field) => !imageInputNames.has(String(field.name || "").toLowerCase()))
        .map((field) => ({ label: field.label || field.name, token: `{{ input.${field.name} }}`, type: field.type || "TEXT" })),
    ];
    const upstream = upstreamNodeKeys(node);
    const nodes = (currentVersion()?.nodes || []).filter((item) => (
      item.id !== node?.id
      && item.enabled
      && item.node_type !== "START"
      && upstream.has(item.node_key)
    ));
    const promptGroups = normalizedScope === "PROMPT"
      ? (() => {
        const keys = promptMappedParameterKeys(node);
        return keys.length
          ? [{
            label: "本节点提示词参数",
            description: "来自“输入”页签的提示词参数映射",
            items: keys.map((key) => ({ label: key, token: `{{ params.${key} }}`, type: "ANY" })),
          }]
          : [];
      })()
      : [];
    const loopItemGroup = node?.node_type === "LOOP" && normalizedScope === "PROMPT"
      ? [{ label: "当前循环项", description: "每张裁剪图执行一次时自动替换", items: [
        { label: "目标类别", token: "{{ item.label }}", type: "TEXT" },
        { label: "目标编号", token: "{{ item.index }}", type: "NUMBER" },
        { label: "目标置信度", token: "{{ item.confidence }}", type: "NUMBER" },
        { label: "目标原图坐标", token: "{{ item.bbox }}", type: "BBOX" },
        { label: "当前裁剪图片", token: "{{ item.image_path }}", type: "IMAGE" },
      ] }] : [];
    return [
      ...promptGroups,
      ...loopItemGroup,
      { label: "开始 / 系统输入", description: "接口入参和开始节点字段", items: inputItems },
      ...nodes.map((item) => ({
        label: `${item.name} · ${item.node_key}`,
        description: item.node_type === "VISION_MODEL"
          ? `${selectedVisionModelVersion(item.config_json?.model_version_id)?.model_name || "未选择模型"} · ${visionOutputProfile(selectedVisionModelVersion(item.config_json?.model_version_id)).display_name}`
          : nodeTypeLabel(item.node_type),
        items: [
          ...nodeOutputDescriptors(item).map((descriptor) => ({ label: descriptor.label, token: `{{ nodes.${item.node_key}.${descriptor.name} }}`, type: descriptor.type })),
          { label: "完整输出", token: `{{ nodes.${item.node_key} }}`, type: "JSON" },
        ],
      })),
    ];
  }

  function variableAcceptedTypes(target) {
    return String(target?.dataset?.variableAcceptTypes || "")
      .split("|")
      .map((item) => normalizeWorkflowDataType(item))
      .filter((item) => item && item !== "ANY");
  }

  function variableTypeIsCompatible(item, target) {
    const accepted = variableAcceptedTypes(target);
    if (!accepted.length) return true;
    const actual = normalizeWorkflowDataType(item?.type);
    return actual === "ANY" || accepted.includes(actual);
  }

  function isEditableVariableTarget(target) {
    if (isPromptTemplateEditor(target)) {
      return Boolean(target.isConnected && target.getAttribute("contenteditable") === "true");
    }
    return Boolean(
      target
      && target.isConnected
      && target.matches?.("[data-variable-target]")
      && !target.disabled
      && !target.readOnly
    );
  }

  function insertVariableToken(token, target = state.variableTarget) {
    if (!isEditableVariableTarget(target)) return false;
    if (isPromptTemplateEditor(target)) return insertPromptVariableChip(token, target);
    const value = String(target.value || "");
    const start = Number.isInteger(target.selectionStart) ? target.selectionStart : value.length;
    const end = Number.isInteger(target.selectionEnd) ? target.selectionEnd : value.length;
    target.value = `${value.slice(0, start)}${token}${value.slice(end)}`;
    const cursor = start + token.length;
    target.focus();
    target.setSelectionRange?.(cursor, cursor);
    target.dispatchEvent(new Event("input", { bubbles: true }));
    state.variableTarget = target;
    return true;
  }

  function replaceVariableToken(token, target = state.variableTarget) {
    if (!isEditableVariableTarget(target)) return false;
    if (isPromptTemplateEditor(target)) return insertPromptVariableChip(token, target);
    target.value = token;
    target.focus();
    target.setSelectionRange?.(token.length, token.length);
    target.dispatchEvent(new Event("input", { bubbles: true }));
    state.variableTarget = target;
    return true;
  }

  function variablePickerElement() {
    let picker = byId("workflowVariablePicker");
    if (picker) return picker;
    picker = document.createElement("section");
    picker.id = "workflowVariablePicker";
    picker.className = "workflow-variable-picker";
    picker.hidden = true;
    document.body.appendChild(picker);
    return picker;
  }

  function closeVariablePicker() {
    const picker = byId("workflowVariablePicker");
    if (picker) picker.hidden = true;
    state.variablePickerQuery = "";
  }

  function variableMatchesQuery(item, group, query) {
    if (!query) return true;
    return `${group.label} ${group.description || ""} ${item.label} ${item.token}`.toLocaleLowerCase().includes(query);
  }

  function renderVariablePicker() {
    const target = state.variableTarget;
    const node = nodeById(state.selectedNodeId);
    if (!isEditableVariableTarget(target) || !node) {
      closeVariablePicker();
      return;
    }
    const picker = variablePickerElement();
    const query = String(state.variablePickerQuery || "").trim().toLocaleLowerCase();
    const scope = variableScopeForTarget(target);
    const accepted = variableAcceptedTypes(target);
    const groups = variableGroups(node, scope)
      .map((group) => ({ ...group, items: group.items.filter((item) => (
        variableMatchesQuery(item, group, query) && variableTypeIsCompatible(item, target)
      )) }))
      .filter((group) => group.items.length);
    const list = groups.length
      ? groups.map((group) => `<section class="workflow-variable-group"><header><strong>${escapeHtml(group.label)}</strong>${group.description ? `<small>${escapeHtml(group.description)}</small>` : ""}</header>${group.items.map((item) => `<button type="button" class="workflow-variable-option" data-variable-picker-token="${escapeHtml(item.token)}"><span>${escapeHtml(item.label)}</span><em class="workflow-data-type ${workflowTypeClass(item.type)}">${escapeHtml(workflowTypeLabel(item.type))}</em><code>${escapeHtml(item.token)}</code></button>`).join("")}</section>`).join("")
      : `<div class="workflow-variable-empty">${accepted.length ? `没有可用于“${accepted.map(workflowTypeLabel).join(" / ")}”的上游变量` : "没有匹配的可用变量"}</div>`;
    const promptHint = scope === "PROMPT"
      ? `<div class="workflow-variable-prompt-hint">${promptMappedParameterKeys(node).length ? "优先使用本节点提示词参数；它们会在运行时替换成映射值。" : "先到“输入”页签新增提示词参数映射，再插入 {{ params.参数键 }}。"}</div>`
      : "";
    picker.innerHTML = `<header class="workflow-variable-picker-heading"><div><strong>选择变量</strong><small>仅展示当前节点可以使用的输入和上游输出</small></div><button type="button" data-variable-picker-close aria-label="关闭变量选择">×</button></header>${promptHint}<label class="workflow-variable-search"><span aria-hidden="true">⌕</span><input id="workflowVariablePickerSearch" type="search" value="${escapeHtml(state.variablePickerQuery)}" placeholder="搜索节点、字段或变量" autocomplete="off"></label><div class="workflow-variable-picker-list">${list}</div>`;
    const rect = target.getBoundingClientRect();
    const width = Math.min(440, Math.max(300, rect.width + 76));
    let left = Math.min(Math.max(12, rect.left), Math.max(12, window.innerWidth - width - 12));
    let top = rect.bottom + 8;
    const maxHeight = Math.min(380, Math.max(220, window.innerHeight - 28));
    if (top + maxHeight > window.innerHeight - 12) top = Math.max(12, rect.top - maxHeight - 8);
    picker.style.width = `${width}px`;
    picker.style.left = `${left}px`;
    picker.style.top = `${top}px`;
    picker.style.maxHeight = `${maxHeight}px`;
    picker.hidden = false;
    requestAnimationFrame(() => byId("workflowVariablePickerSearch")?.focus());
  }

  function openVariablePicker(target) {
    if (!isEditableVariableTarget(target)) return;
    if (isPromptTemplateEditor(target)) capturePromptSelection(target);
    state.variableTarget = target;
    state.variablePickerQuery = "";
    renderVariablePicker();
  }

  function applyVariablePickerToken(token) {
    const target = state.variableTarget;
    if (!isEditableVariableTarget(target)) return;
    const mode = String(target.dataset.variableMode || "REPLACE").toUpperCase();
    const applied = mode === "INSERT" ? insertVariableToken(token, target) : replaceVariableToken(token, target);
    if (applied) closeVariablePicker();
  }

  function parseJson(value, label, fallback = {}) {
    const text = String(value || "").trim();
    if (!text) return fallback;
    try { return JSON.parse(text); } catch { throw new Error(`${label}必须是有效 JSON。`); }
  }
  function inputMappingMarkup(config, node, readonly, {
    title = "节点输入参数",
    description = "点击参数值右侧 { } 选择开始输入或上游节点输出；输入 { 也可打开选择器。",
  } = {}) {
    return parameterSectionMarkup({
      id: "nodeInputParameters",
      title,
      description,
      rows: mappingRows(config.input_mapping ?? config.context ?? {}),
      readonly,
      keyPlaceholder: "例如 expected_text",
      valuePlaceholder: "例如 {{ input.expected_text }}",
      variableScope: "INPUT",
    });
  }

  function ruleInputValue(config, key) {
    const mapping = config.input_mapping ?? config.context ?? {};
    return parameterValue(config[key]).replace(/\{\{\s*params\.([A-Za-z_][A-Za-z0-9_]*)\s*\}\}/g, (token, name) => (
      Object.prototype.hasOwnProperty.call(mapping, name) ? parameterValue(mapping[name]) : token
    ));
  }

  function ruleActualSourceHint(value) {
    const reference = /^\s*\{\{\s*nodes\.([\w-]+)\.([\w.-]+)\s*\}\}\s*$/.exec(String(value || ""));
    if (!reference) {
      const inputReference = /^\s*\{\{\s*input\.([\w-]+)\s*\}\}\s*$/.exec(String(value || ""));
      return inputReference
        ? `当前来源：开始节点输入 → ${inputReference[1]}`
        : "请选择上游模型的具体输出；有多个模型时，请核对节点名称和字段。";
    }
    const source = nodeByKey(reference[1]);
    if (!source) return `变量来源 ${reference[1]} 不存在，请重新选择。`;
    const field = reference[2];
    const descriptor = nodeOutputDescriptors(source).find((item) => item.name === field);
    const version = source.node_type === "VISION_MODEL" ? selectedVisionModelVersion(source.config_json?.model_version_id) : null;
    const modelLabel = version ? ` · ${version.model_name} / ${visionOutputProfile(version).display_name}` : "";
    const fieldLabel = descriptor ? `${descriptor.label}（${workflowTypeLabel(descriptor.type)}）` : field;
    return `当前来源：${source.name}（${source.node_key}）${modelLabel} → ${fieldLabel}`;
  }

  function updateRuleActualSourceHint() {
    const field = byId("nodeActual");
    const hint = byId("nodeActualSource");
    if (field && hint) hint.textContent = ruleActualSourceHint(field.value);
  }

  const ruleOperators = [
    ["EQUALS", "等于"],
    ["CONTAINS", "包含"],
    ["EXISTS", "存在（非空）"],
    ["NUMBER_EQUALS", "数值等于"],
    ["NUMBER_GT", "数值大于"],
    ["NUMBER_GTE", "数值大于或等于"],
    ["NUMBER_LT", "数值小于"],
    ["NUMBER_LTE", "数值小于或等于"],
  ];

  function promptParameterMappingMarkup(config, node, readonly) {
    return inputMappingMarkup(config, node, readonly, {
      title: "提示词参数映射",
      description: "先设置参数键和值；提示词中使用 {{ params.参数键 }}，运行时会替换为对应的实际值。右侧 { } 可选择开始输入或上游输出。",
    });
  }

  function outputMappingMarkup(config, readonly, { id = "nodeOutputParameters", title = "节点输出参数", description = "为当前节点的原始输出声明易读别名，供后续节点引用。", variableScope = "OUTPUT" } = {}) {
    return parameterSectionMarkup({
      id,
      title,
      description: `${description} 点击参数值右侧 { } 选择输出字段。`,
      rows: mappingRows(config.output_mapping || {}),
      readonly,
      keyPlaceholder: "例如 detected_text",
      valuePlaceholder: "例如 {{ response.reason }}",
      variableScope,
    });
  }
  function modelVersionOptions(selected) {
    return `<option value="">请选择已发布模型版本</option>${state.visionVersions.filter((item) => item.available).map((item) => `<option value="${item.version_id}" ${Number(selected) === item.version_id ? "selected" : ""}>${escapeHtml(item.model_name)} · V${escapeHtml(item.version)} · ${escapeHtml(visionOutputProfile(item).display_name)}</option>`).join("")}`;
  }
  function selectedVisionModelVersion(versionId) {
    return state.visionVersions.find((item) => Number(item.version_id) === Number(versionId)) || null;
  }
  function visionTaskLabel(modelOrTask) {
    const profile = visionOutputProfile(modelOrTask);
    return `${profile.display_name} · ${profile.summary}`;
  }
  function visionCapabilityMarkup(profile) {
    const capabilities = [
      [profile.supports_objects, "目标列表"],
      [profile.supports_bbox, "bbox 定位框"],
      [profile.supports_mask, "mask 轮廓"],
      [profile.supports_classification, "Top1 / Top5 分类"],
    ].filter(([supported]) => supported).map(([, label]) => `<span>${label}</span>`);
    return capabilities.length
      ? `<div class="node-model-capability-list">${capabilities.join("")}</div>`
      : '<p class="node-model-compatibility-warning">当前模型未声明几何定位能力，不能作为“图片裁剪”的 bbox 来源。</p>';
  }
  function cropBboxSourceHint(node) {
    const upstream = (currentVersion()?.nodes || []).filter((item) => (
      item.enabled && item.node_type === "VISION_MODEL" && upstreamNodeKeys(node).has(item.node_key)
    ));
    if (!upstream.length) return '<p>可填写固定坐标，或先连接一个上游目标检测/分割节点。</p>';
    const supported = upstream.filter((item) => visionOutputProfile(selectedVisionModelVersion(item.config_json?.model_version_id)).supports_bbox);
    const unsupported = upstream.filter((item) => !visionOutputProfile(selectedVisionModelVersion(item.config_json?.model_version_id)).supports_bbox);
    return `<p>${supported.length ? `可用 bbox 来源：${supported.map((item) => `<code>${escapeHtml(item.name)}</code>`).join("、")}。` : "当前上游没有可用 bbox 来源，请改用固定坐标或接入目标检测/分割模型。"}${unsupported.length ? `<br><span class="node-model-compatibility-warning">${unsupported.map((item) => escapeHtml(item.name)).join("、")}不输出 bbox，不能作为裁剪框来源。</span>` : ""}</p>`;
  }
  function cropBboxCompatibilityError(value) {
    const match = String(value || "").trim().match(/^\{\{\s*nodes\.([A-Za-z0-9_-]+)\.objects(?:\.\d+)?\.bbox\s*\}\}$/);
    if (!match) return null;
    const source = nodeByKey(match[1]);
    if (!source || source.node_type !== "VISION_MODEL") return null;
    const profile = visionOutputProfile(selectedVisionModelVersion(source.config_json?.model_version_id));
    return profile.supports_bbox ? null : `“${source.name}”是${profile.display_name}，不输出 bbox，不能作为裁剪框来源。`;
  }

  function annotationSourceHint(node) {
    const upstream = (currentVersion()?.nodes || []).filter((item) => (
      item.enabled && item.node_type === "VISION_MODEL" && upstreamNodeKeys(node).has(item.node_key)
    ));
    if (!upstream.length) return '<p>可填写固定 bbox，或先连接一个上游目标检测/分割节点。</p>';
    const geometry = upstream.filter((item) => visionOutputProfile(selectedVisionModelVersion(item.config_json?.model_version_id)).supports_bbox);
    const classification = upstream.filter((item) => !visionOutputProfile(selectedVisionModelVersion(item.config_json?.model_version_id)).supports_bbox);
    return `<p>${geometry.length ? `可画框模型：${geometry.map((item) => `<code>${escapeHtml(item.name)}</code>`).join("、")}。推荐把“目标列表”设为 <code>{{ nodes.模型节点.objects }}</code>。` : "当前上游没有可画框模型，请填写固定 bbox 或接入目标检测/分割模型。"}${classification.length ? `<br><span class="node-model-compatibility-warning">${classification.map((item) => escapeHtml(item.name)).join("、")}为分类模型，不输出坐标，不能用于画框。</span>` : ""}</p>`;
  }

  function annotationGeometryCompatibilityError(objects, bbox) {
    const objectMatch = String(objects || "").trim().match(/^\{\{\s*nodes\.([A-Za-z0-9_-]+)\.objects\s*\}\}$/);
    const bboxMatch = String(bbox || "").trim().match(/^\{\{\s*nodes\.([A-Za-z0-9_-]+)\.objects(?:\.\d+)?\.bbox\s*\}\}$/);
    const match = objectMatch || bboxMatch;
    if (!match) return null;
    const source = nodeByKey(match[1]);
    if (!source || source.node_type !== "VISION_MODEL") return null;
    const profile = visionOutputProfile(selectedVisionModelVersion(source.config_json?.model_version_id));
    if (objectMatch && !profile.supports_objects) return `“${source.name}”是${profile.display_name}，不输出目标列表，不能用于画框。`;
    if (bboxMatch && !profile.supports_bbox) return `“${source.name}”是${profile.display_name}，不输出 bbox，不能用于画框。`;
    return null;
  }

  function workflowVlmMode(config) {
    const configured = String(config?.vlm_mode || "").toUpperCase();
    if (configured === "SCENE" || configured === "CUSTOM") return configured;
    return config?.referenced_scenario_version_id ? "SCENE" : "CUSTOM";
  }

  function readVlmInspectorConfig(node, { mode = workflowVlmMode(node?.config_json), validate = false, removeInactive = false } = {}) {
    const config = deepCopy(node?.config_json);
    const normalizedMode = String(mode).toUpperCase() === "SCENE" ? "SCENE" : "CUSTOM";
    const inputRows = byId("nodeInputParameters");
    const outputRows = byId("nodeOutputParameters");
    if (inputRows) {
      config.input_mapping = readParameterRows("nodeInputParameters", "节点输入参数");
      config.context = config.input_mapping;
    }
    if (outputRows) config.output_mapping = readParameterRows("nodeOutputParameters", "节点输出参数");
    config.vlm_mode = normalizedMode;
    if (normalizedMode === "SCENE") {
      const selected = byId("nodeReferencedScene")?.value;
      const referenced = selected === undefined
        ? (Number(config.referenced_scenario_version_id) || null)
        : (Number(selected) || null);
      if (validate && !referenced) throw new Error("请选择已发布的 VLM 场景。");
      config.referenced_scenario_version_id = referenced;
      if (removeInactive) {
        delete config.vlm_model_id;
        delete config.prompt;
        delete config.vlm_parameters;
      }
      return config;
    }
    const modelValue = byId("nodeVlmModel")?.value;
    const modelId = modelValue === undefined
      ? (Number(config.vlm_model_id) || null)
      : (Number(modelValue) || null);
    const promptField = byId("nodePrompt");
    const promptEditor = byId("nodePromptEditor");
    const prompt = String(promptEditor ? syncPromptTemplateEditor(promptEditor) : (promptField ? promptField.value : (config.prompt || ""))).trim();
    const temperatureValue = byId("nodeTemperature")?.value;
    const maxTokensValue = byId("nodeMaxTokens")?.value;
    if (validate && (!modelId || !prompt)) throw new Error("自定义 VLM 节点需要选择模型并填写提示词。");
    config.vlm_model_id = modelId;
    config.prompt = prompt;
    config.vlm_parameters = {
      temperature: Number(temperatureValue ?? config.vlm_parameters?.temperature ?? 0),
      max_tokens: Number(maxTokensValue ?? config.vlm_parameters?.max_tokens ?? 1024),
    };
    if (removeInactive) delete config.referenced_scenario_version_id;
    return config;
  }

  function directSceneOptions(selected) {
    return `<option value="">请选择已发布 VLM 场景</option>${state.publishedScenes.filter((item) => item.mode === "VLM_DIRECT").map((item) => `<option value="${item.scenario_version_id}" ${Number(selected) === Number(item.scenario_version_id) ? "selected" : ""}>${escapeHtml(item.scenario_name)} · V${escapeHtml(item.version)}</option>`).join("")}`;
  }

  function updateWorkflowWorkbenchPanels() {
    const workbench = byId("workflowDesigner");
    if (!workbench) return;
    workbench.classList.toggle("workflow-palette-collapsed", Boolean(state.paletteCollapsed));
    workbench.classList.toggle("workflow-inspector-collapsed", Boolean(state.inspectorCollapsed));
    const paletteToggle = byId("workflowPaletteToggle");
    const inspectorToggle = byId("workflowInspectorToggle");
    if (paletteToggle) {
      paletteToggle.classList.toggle("is-active", !state.paletteCollapsed);
      paletteToggle.setAttribute("aria-expanded", String(!state.paletteCollapsed));
    }
    if (inspectorToggle) {
      inspectorToggle.classList.toggle("is-active", !state.inspectorCollapsed);
      inspectorToggle.setAttribute("aria-expanded", String(!state.inspectorCollapsed));
    }
  }

  function setWorkbenchPanel(panel, collapsed) {
    if (panel === "palette") state.paletteCollapsed = Boolean(collapsed);
    if (panel === "inspector") state.inspectorCollapsed = Boolean(collapsed);
    closeVariablePicker();
    updateWorkflowWorkbenchPanels();
  }

  function openInspectorForNode(tab = "SETTINGS") {
    state.inspectorTab = tab;
    state.inspectorCollapsed = false;
    closeVariablePicker();
    updateWorkflowWorkbenchPanels();
  }

  function setInspectorTab(tab) {
    const target = byId("workflowInspectorContent");
    if (!target) return;
    const availableTabs = [...target.querySelectorAll("[data-inspector-tab]")]
      .filter((button) => !button.hidden)
      .map((button) => button.dataset.inspectorTab);
    if (!availableTabs.includes(tab)) return;
    state.inspectorTab = tab;
    closeVariablePicker();
    target.querySelectorAll("[data-inspector-tab]").forEach((button) => {
      const active = button.dataset.inspectorTab === tab;
      button.classList.toggle("is-active", active);
      button.setAttribute("aria-selected", String(active));
    });
    target.querySelectorAll("[data-inspector-panel]").forEach((panel) => {
      panel.hidden = panel.dataset.inspectorPanel !== tab;
    });
  }

  function setupInspectorTabs(target) {
    const settingsPanel = target.querySelector('[data-inspector-panel="SETTINGS"]');
    const inputPanel = target.querySelector('[data-inspector-panel="INPUT"]');
    const outputPanel = target.querySelector('[data-inspector-panel="OUTPUT"]');
    const contractPanel = target.querySelector('[data-inspector-panel="CONTRACT"]');
    const inputContent = target.querySelector("#workflowNodeInputContent");
    const outputContent = target.querySelector("#workflowNodeOutputContent");
    const contractContent = target.querySelector("#workflowNodeContractContent");
    const parameterSection = (element) => element?.closest(".node-parameter-section") || element;
    const inputSections = [...new Set([...target.querySelectorAll("#nodeInputParameters, #nodeStartInputParameters")]
      .map(parameterSection)
      .filter(Boolean))];
    const outputSections = [...new Set([...target.querySelectorAll("#nodeOutputParameters, #nodeEndOutputParameters")]
      .map(parameterSection)
      .filter(Boolean))];
    inputSections.forEach((section) => inputContent?.append(section));
    outputSections.forEach((section) => outputContent?.append(section));
    const node = nodeById(state.selectedNodeId);
    const unifiedInput = node && !["START", "END"].includes(node.node_type);
    if (unifiedInput) {
      const settingsContent = target.querySelector("#workflowNodeSettingsContent");
      inputContent?.prepend(...settingsContent.children);
      if (settingsPanel) settingsPanel.hidden = true;
    }
    if (inputPanel) inputPanel.hidden = !unifiedInput && inputSections.length === 0;
    if (outputPanel) outputPanel.hidden = outputSections.length === 0;
    if (contractContent && node) contractContent.innerHTML = workflowNodeContractMarkup(node);
    if (contractPanel) contractPanel.hidden = !node;

    const tabList = target.querySelector(".node-config-tabs");
    const tabs = [
      { key: "SETTINGS", label: "设置", panel: settingsPanel },
      { key: "INPUT", label: "输入", panel: inputPanel },
      { key: "OUTPUT", label: "输出", panel: outputPanel },
      { key: "CONTRACT", label: "契约", panel: contractPanel },
    ].filter((item) => item.panel && !item.panel.hidden);
    if (tabList) {
      tabList.innerHTML = tabs.map((item) => `<button type="button" class="node-config-tab" data-inspector-tab="${item.key}" role="tab" aria-selected="false">${item.label}</button>`).join("");
    }
    if (!tabs.some((item) => item.key === state.inspectorTab)) state.inspectorTab = tabs[0]?.key || "SETTINGS";
    setInspectorTab(state.inspectorTab);
  }

  const loopBodyTypes = ["VLM", "VISION_MODEL", "IMAGE_CROP", "IMAGE_ANNOTATE", "RULE", "IF", "WEB_API", "S3_UPLOAD"];
  const loopTypes = new Set(["LOOP", "ITERATION", "CONDITION_LOOP"]);

  function conditionUpdateRowMarkup(key = "", update = {}, readonly = false) {
    const operation = typeof update === "object" && update !== null ? update.operation || "SET" : "SET";
    const value = operation === "SET" ? update : update.value;
    return `<div class="condition-update-row" data-condition-update-row><input class="form-control form-control-sm" data-update-key aria-label="循环变量名" placeholder="例如 count" value="${escapeHtml(key)}" ${readonly ? "readonly" : ""}><select class="form-select form-select-sm" data-update-operation aria-label="更新方式" ${readonly ? "disabled" : ""}><option value="SET" ${operation === "SET" ? "selected" : ""}>赋值</option><option value="INCREMENT" ${operation === "INCREMENT" ? "selected" : ""}>累加</option><option value="APPEND" ${operation === "APPEND" ? "selected" : ""}>追加到数组</option></select><input class="form-control form-control-sm" data-update-value aria-label="更新值" placeholder="{{ nodes.body_rule_1.actual }}" value="${escapeHtml(parameterValue(value))}" ${readonly ? "readonly" : ""}>${readonly ? "" : '<button type="button" class="btn btn-outline-danger btn-sm" data-update-remove title="删除变量">×</button>'}</div>`;
  }

  function conditionUpdatesMarkup(updates, readonly) {
    const rows = Object.entries(updates || {}).map(([key, update]) => conditionUpdateRowMarkup(key, update, readonly)).join("");
    return `<section class="node-parameter-section"><div class="node-parameter-heading"><div><strong>每轮变量更新</strong><small>子流程执行后更新变量；可引用内部节点输出，例如 {{ nodes.body_rule_1.actual }}。</small></div>${readonly ? "" : '<button class="node-parameter-add" type="button" id="nodeLoopUpdateAdd">＋ 添加变量</button>'}</div><div class="condition-update-rows" id="nodeLoopUpdates">${rows}</div></section>`;
  }

  function readConditionUpdates() {
    const updates = {};
    byId("nodeLoopUpdates")?.querySelectorAll("[data-condition-update-row]").forEach((row) => {
      const key = row.querySelector("[data-update-key]").value.trim();
      const value = row.querySelector("[data-update-value]").value.trim();
      const operation = row.querySelector("[data-update-operation]").value;
      if (!key && !value) return;
      if (!parameterKeyPattern.test(key)) throw new Error("循环变量名只能使用字母、数字和下划线，且不能以数字开头。");
      if (Object.hasOwn(updates, key)) throw new Error(`循环变量“${key}”重复。`);
      if (operation === "INCREMENT" && !value) throw new Error(`循环变量“${key}”需要累加值。`);
      updates[key] = operation === "SET" ? value : { operation, value };
    });
    return updates;
  }

  function readInitialState() {
    return Object.fromEntries(Object.entries(readParameterRows("nodeLoopInitialState", "初始循环变量")).map(([key, value]) => {
      try { return [key, JSON.parse(value)]; } catch { return [key, value]; }
    }));
  }

  function readLoopBody() {
    syncLoopBodyEditor();
    const body = deepCopy(state.loopBodyDraft);
    if (!body.length) throw new Error("请先在子流程中添加处理节点。");
    body.forEach((child) => {
      if (child.node_type !== "WEB_API") return;
      const config = child.config_json || {};
      config.headers = parseJson(config.headers, `${child.name} 请求头`, {});
      if (config.request_format !== "TEXT" && typeof config.body === "string" && !/^\{\{[^{}]+\}\}$/.test(config.body.trim())) {
        config.body = parseJson(config.body, `${child.name} 请求体`, {});
      }
    });
    return body;
  }

  function loopBodyField(label, field, value, { multiline = false, placeholder = "", type = "text" } = {}) {
    const common = `data-loop-field="${field}" class="form-control form-control-sm" placeholder="${escapeHtml(placeholder)}"`;
    return `<label><span>${label}</span>${multiline
      ? `<textarea ${common} rows="3">${escapeHtml(value || "")}</textarea>`
      : `<input ${common} type="${type}" value="${escapeHtml(value ?? "")}">`}</label>`;
  }

  function loopBodyNodeFields(entry) {
    const config = entry.config_json || {};
    const imageField = () => loopBodyField("输入图片", "image_path", config.image_path || "{{ input.image_path }}", { placeholder: "{{ input.image_path }}" });
    if (entry.node_type === "VLM") return `${imageField()}<label><span>VLM 模型</span><select class="form-select form-select-sm" data-loop-field="vlm_model_id"><option value="">请选择</option>${state.vlms.filter((item) => item.enabled).map((item) => `<option value="${item.id}" ${Number(config.vlm_model_id) === item.id ? "selected" : ""}>${escapeHtml(item.name)}</option>`).join("")}</select></label>${loopBodyField("检测提示词", "prompt", config.prompt || "", { multiline: true, placeholder: "检查 {{ item.label }}，返回 result 和 reason" })}${loopBodyField("温度", "temperature", config.vlm_parameters?.temperature ?? 0, { type: "number" })}${loopBodyField("最大输出 Token", "max_tokens", config.vlm_parameters?.max_tokens ?? 1024, { type: "number" })}`;
    if (entry.node_type === "VISION_MODEL") return `${imageField()}<label><span>已发布模型</span><select class="form-select form-select-sm" data-loop-field="model_version_id">${modelVersionOptions(config.model_version_id)}</select></label>${loopBodyField("置信度阈值", "confidence", config.confidence ?? 0.25, { type: "number" })}${loopBodyField("IoU 阈值", "iou", config.iou ?? 0.45, { type: "number" })}`;
    if (entry.node_type === "IMAGE_CROP") return `${imageField()}${loopBodyField("目标列表（批量裁剪）", "objects", config.objects || "", { placeholder: "{{ nodes.body_model_1.objects }}" })}${loopBodyField("单个 bbox（与目标列表二选一）", "bbox", config.bbox || "", { placeholder: "{{ input.item.bbox }}" })}${loopBodyField("边缘扩展比例", "padding_ratio", config.padding_ratio ?? 0.05, { type: "number" })}`;
    if (entry.node_type === "IMAGE_ANNOTATE") return `${imageField()}${loopBodyField("目标列表", "objects", config.objects || "", { placeholder: "{{ nodes.body_model_1.objects }}" })}${loopBodyField("单个 bbox", "bbox", config.bbox || "", { placeholder: "{{ input.item.bbox }}" })}`;
    if (["RULE", "IF"].includes(entry.node_type)) return `${loopBodyField("实际值", "actual", config.actual || "", { placeholder: "{{ nodes.body_model_1.predicted_label }}" })}<label><span>运算符</span><select class="form-select form-select-sm" data-loop-field="operator">${ruleOperators.map(([operator, label]) => `<option value="${operator}" ${config.operator === operator ? "selected" : ""}>${label}</option>`).join("")}</select></label>${loopBodyField("期望值", "expected", config.expected ?? "", { placeholder: "{{ input.expected_label }}" })}`;
    if (entry.node_type === "WEB_API") return `<label><span>请求方法</span><select class="form-select form-select-sm" data-loop-field="method">${["GET", "POST", "PUT", "PATCH", "DELETE"].map((method) => `<option value="${method}" ${(config.method || "POST") === method ? "selected" : ""}>${method}</option>`).join("")}</select></label>${loopBodyField("接口地址", "url", config.url || "", { placeholder: "https://..." })}${loopBodyField("请求头 JSON", "headers", typeof config.headers === "string" ? config.headers : JSON.stringify(config.headers || {}), { multiline: true })}<label><span>请求格式</span><select class="form-select form-select-sm" data-loop-field="request_format"><option value="JSON" ${(config.request_format || "JSON") === "JSON" ? "selected" : ""}>JSON</option><option value="TEXT" ${config.request_format === "TEXT" ? "selected" : ""}>文本</option></select></label>${loopBodyField("请求体（支持变量）", "body", typeof config.body === "string" ? config.body : JSON.stringify(config.body || {}), { multiline: true })}<label><span>响应格式</span><select class="form-select form-select-sm" data-loop-field="response_format"><option value="JSON" ${(config.response_format || "JSON") === "JSON" ? "selected" : ""}>JSON</option><option value="TEXT" ${config.response_format === "TEXT" ? "selected" : ""}>文本</option></select></label>`;
    if (entry.node_type === "S3_UPLOAD") return `${imageField()}${loopBodyField("对象键（可选）", "key", config.key || "")}`;
    return "";
  }

  function renderLoopBodyEditor() {
    const target = byId("nodeLoopBodyEditor");
    if (!target || !state.loopBodyDraft) return;
    const readonly = !isDraft();
    const nodes = state.loopBodyDraft;
    const destinationOptions = (index, selected, fallback) => {
      const destinations = [...nodes.slice(index + 1).map((item) => [item.node_key, item.name || item.node_key]), ["loop_end", "循环结束"]];
      if (selected && !destinations.some(([key]) => key === selected)) destinations.unshift([selected, `无效目标：${selected}`]);
      return destinations.map(([key, label]) => `<option value="${escapeHtml(key)}" ${(selected || fallback) === key ? "selected" : ""}>${escapeHtml(label)}</option>`).join("");
    };
    const conditionLoop = nodeById(state.selectedNodeId)?.node_type === "CONDITION_LOOP";
    target.innerHTML = `<div class="loop-body-heading"><strong>${conditionLoop ? "循环体 · 每轮执行一次" : "子流程 · 每项执行一次"}</strong><small>${nodes.length} 个处理节点</small><button type="button" class="btn btn-outline-primary btn-sm" data-subflow-inspector-open>在画布中展开 ↓</button></div><div class="loop-body-track"><div class="loop-body-boundary">▶ ${conditionLoop ? "本轮开始" : "本项开始"} <code>${conditionLoop ? "state.* / input.item.*" : "input.item.*"}</code></div>${nodes.map((entry, index) => {
      const nextKey = nodes[index + 1]?.node_key || "loop_end";
      const routing = entry.node_type === "IF"
        ? `<div class="loop-body-routing"><label><span>条件成立 →</span><select class="form-select form-select-sm" data-loop-route="true_next_node_key">${destinationOptions(index, entry.true_next_node_key, nextKey)}</select></label><label><span>条件不成立 →</span><select class="form-select form-select-sm" data-loop-route="false_next_node_key">${destinationOptions(index, entry.false_next_node_key, "loop_end")}</select></label></div>`
        : `<div class="loop-body-routing"><label><span>执行后连接 →</span><select class="form-select form-select-sm" data-loop-route="next_node_key">${destinationOptions(index, entry.next_node_key, nextKey)}</select></label></div>`;
      return `<article class="loop-body-card" data-loop-index="${index}"><header><span>${index + 1}. ${escapeHtml(nodeTypeLabel(entry.node_type))}</span><input class="form-control form-control-sm" data-loop-name value="${escapeHtml(entry.name || entry.node_key)}" aria-label="节点名称" ${readonly ? "readonly" : ""}><code>${escapeHtml(entry.node_key)}</code>${readonly ? "" : `<button type="button" class="btn btn-outline-secondary btn-sm" data-loop-move="up" data-loop-index="${index}" ${index === 0 ? "disabled" : ""} title="上移">↑</button><button type="button" class="btn btn-outline-secondary btn-sm" data-loop-move="down" data-loop-index="${index}" ${index === nodes.length - 1 ? "disabled" : ""} title="下移">↓</button><button type="button" class="btn btn-outline-danger btn-sm" data-loop-remove="${index}" title="删除节点">×</button>`}</header><div class="loop-body-fields">${loopBodyNodeFields(entry)}${routing}</div></article><div class="loop-body-arrow">↓</div>`;
    }).join("")}<div class="loop-body-boundary">■ ${conditionLoop ? "本轮结束" : "本项结束"} · 收集最后节点输出</div></div>${readonly ? "" : `<div class="loop-body-add"><select id="nodeLoopAddType" class="form-select form-select-sm">${loopBodyTypes.map((type) => `<option value="${type}">${escapeHtml(nodeTypeLabel(type))}</option>`).join("")}</select><button type="button" class="btn btn-outline-primary btn-sm" id="nodeLoopAdd">＋ 添加内部节点</button></div>`}<p class="muted-copy">默认顺序连接；条件分支可分别选择后续节点。${conditionLoop ? '本轮状态用 <code>{{ state.变量名 }}</code>，轮次用 <code>{{ input.item.index }}</code>。' : '当前项用 <code>{{ input.item.* }}</code>。'}内部上游用 <code>{{ nodes.节点键.* }}</code>；外层用 <code>{{ parent.nodes.节点键.* }}</code>。</p>`;
    target.querySelectorAll("input, textarea, select").forEach((field) => { if (readonly) field.disabled = true; });
  }

  function syncLoopBodyEditor() {
    if (!state.loopBodyDraft) return;
    byId("nodeLoopBodyEditor")?.querySelectorAll("[data-loop-index].loop-body-card").forEach((card) => {
      const entry = state.loopBodyDraft[Number(card.dataset.loopIndex)];
      entry.name = card.querySelector("[data-loop-name]")?.value.trim() || entry.node_key;
      const config = entry.config_json || {};
      card.querySelectorAll("[data-loop-field]").forEach((field) => {
        const key = field.dataset.loopField;
        const value = ["vlm_model_id", "model_version_id"].includes(key) ? (Number(field.value) || null)
          : ["confidence", "iou", "padding_ratio", "temperature", "max_tokens"].includes(key) ? Number(field.value) : field.value.trim();
        if (["temperature", "max_tokens"].includes(key)) {
          config.vlm_parameters = { ...(config.vlm_parameters || {}), [key]: value };
        } else config[key] = value;
      });
      card.querySelectorAll("[data-loop-route]").forEach((field) => {
        const route = field.dataset.loopRoute;
        const nextKey = state.loopBodyDraft[Number(card.dataset.loopIndex) + 1]?.node_key || "loop_end";
        const defaultTarget = route === "false_next_node_key" ? "loop_end" : nextKey;
        if (field.value === defaultTarget) delete entry[route];
        else entry[route] = field.value;
      });
      entry.config_json = config;
    });
  }

  function addLoopBodyNode(selectedType = null) {
    syncLoopBodyEditor();
    const type = selectedType || byId("nodeLoopAddType")?.value;
    if (!state.loopBodyDraft || !loopBodyTypes.includes(type)) return;
    if (state.loopBodyDraft.length >= 20) { notify("循环体最多添加 20 个内部节点。", "warning"); return; }
    let sequence = 1;
    const prefix = `body_${type.toLowerCase()}`;
    while (state.loopBodyDraft.some((item) => item.node_key === `${prefix}_${sequence}`)) sequence += 1;
    const key = `${prefix}_${sequence}`;
    state.loopBodyDraft.push({ node_key: key, name: `${nodeTypeLabel(type)}${sequence}`, node_type: type, config_json: defaultNodeConfig(type) });
    renderLoopBodyEditor();
    const canvas = byId("workflowSubflowCanvas");
    if (state.subflowNodeId !== null && canvas) canvas.scrollLeft = canvas.scrollWidth;
  }

  function acceptsLoopDrop(node) {
    return node && loopTypes.has(node.node_type) && (node.node_type !== "LOOP" || node.config_json?.operation === "SUBFLOW");
  }

  function dropNodeIntoLoop(event, nodeId) {
    event.preventDefault();
    event.stopPropagation();
    const type = event.dataTransfer.getData("application/vision-node");
    if (!type) return;
    if (!isDraft()) { notify("已发布版本不可修改，请先新建草稿版本。", "warning"); return; }
    if (!loopBodyTypes.includes(type)) { notify("此节点暂不支持放入循环体。", "warning"); return; }
    if (state.subflowNodeId !== nodeId) openSubflowCanvas(nodeId);
    if (state.subflowNodeId === nodeId) addLoopBodyNode(type);
  }

  function openSubflowCanvas(nodeId) {
    const node = nodeById(nodeId);
    if (!node || !loopTypes.has(node.node_type)) return;
    if (state.subflowNodeId === node.id) { closeSubflowCanvas(); return; }
    const preserveInspector = state.selectedNodeId === node.id && Boolean(byId("nodeLoopBodyEditor"));
    closeSubflowCanvas();
    state.selectedNodeId = node.id;
    state.selectedEdgeId = null;
    openInspectorForNode();
    if (!preserveInspector) renderInspector();
    const editor = byId("nodeLoopBodyEditor");
    if (!editor) return;
    state.subflowNodeId = node.id;
    byId("workflowSubflowTitle").textContent = `${node.name} · ${nodeTypeLabel(node.node_type)}`;
    byId("workflowSubflowCanvas").appendChild(editor);
    byId("workflowSubflowSave").hidden = !isDraft();
    byId("workflowSubflowPanel").hidden = false;
    byId("workflowDesigner").classList.add("subflow-open");
    state.zoom = Math.max(state.zoom, 0.75);
    setWorkbenchPanel("inspector", true);
    renderWorkflowCanvas();
    requestAnimationFrame(() => {
      const viewport = byId("workflowCanvasViewport");
      const position = nodePosition(node);
      const visibleWidth = viewport.clientWidth - 32;
      const panelWidth = Number.parseFloat(byId("workflowSubflowPanel").style.width) || subflowWidth;
      viewport.scrollLeft = Math.max(0, panelWidth * state.zoom > visibleWidth
        ? position.x * state.zoom - 16
        : (position.x + panelWidth / 2) * state.zoom - viewport.clientWidth / 2);
      viewport.scrollTop = Math.max(0, (position.y - 36) * state.zoom - 32);
    });
  }

  function closeSubflowCanvas() {
    if (state.subflowNodeId === null) return;
    syncLoopBodyEditor();
    const editor = byId("nodeLoopBodyEditor");
    const host = byId("nodeLoopBodyHost");
    if (editor && host) host.appendChild(editor);
    byId("workflowSubflowPanel").hidden = true;
    byId("workflowDesigner").classList.remove("subflow-open");
    state.subflowNodeId = null;
    renderWorkflowCanvas();
  }

  function handleLoopBodyAction(event) {
    if (event.target.closest("[data-subflow-inspector-open]")) {
      openSubflowCanvas(state.selectedNodeId);
      return true;
    }
    if (event.target.closest("#nodeLoopAdd")) { addLoopBodyNode(); return true; }
    const loopRemove = event.target.closest("[data-loop-remove]");
    if (loopRemove) {
      syncLoopBodyEditor();
      const [removed] = state.loopBodyDraft.splice(Number(loopRemove.dataset.loopRemove), 1);
      state.loopBodyDraft.forEach((entry) => {
        for (const route of ["next_node_key", "true_next_node_key", "false_next_node_key"]) {
          if (entry[route] === removed.node_key) delete entry[route];
        }
      });
      renderLoopBodyEditor();
      return true;
    }
    const loopMove = event.target.closest("[data-loop-move]");
    if (loopMove) {
      syncLoopBodyEditor();
      const index = Number(loopMove.dataset.loopIndex);
      const target = index + (loopMove.dataset.loopMove === "up" ? -1 : 1);
      if (target >= 0 && target < state.loopBodyDraft.length) {
        [state.loopBodyDraft[index], state.loopBodyDraft[target]] = [state.loopBodyDraft[target], state.loopBodyDraft[index]];
        renderLoopBodyEditor();
      }
      return true;
    }
    return false;
  }

  function renderInspector() {
    const target = byId("workflowInspectorContent");
    const node = nodeById(state.selectedNodeId);
    const inspectorStatus = byId("workflowInspectorStatus");
    if (!node) {
      inspectorStatus.textContent = "未选择";
      target.innerHTML = '<div class="empty-state compact-empty"><span>◇</span><strong>未选择节点</strong><p>选择节点后配置模型、提示词、输入映射和输出。</p></div>';
      return;
    }
    inspectorStatus.textContent = nodeTypeLabel(node.node_type);
    const config = deepCopy(node.config_json);
    if (loopTypes.has(node.node_type) && state.loopBodyNodeId !== node.id) {
      state.loopBodyDraft = Array.isArray(config.body_nodes) ? deepCopy(config.body_nodes) : [];
      state.loopBodyNodeId = node.id;
    }
    const readonly = !isDraft();
    const systemNode = ["START", "END"].includes(node.node_type);
    const identityReadonly = readonly || systemNode;
    const identity = `<div class="form-row"><label><span>节点名称</span><input id="nodeName" class="form-control" value="${escapeHtml(node.name)}" ${identityReadonly ? "readonly" : ""}></label><label><span>节点键</span><input class="form-control" value="${escapeHtml(node.node_key)}" readonly></label></div>`;
    let fields = "";
    if (node.node_type === "VLM") {
      const useScene = workflowVlmMode(config) === "SCENE";
      fields = `<label><span>调用方式</span><select id="nodeVlmMode" class="form-select" ${readonly ? "disabled" : ""}><option value="CUSTOM" ${useScene ? "" : "selected"}>自定义 VLM 检测</option><option value="SCENE" ${useScene ? "selected" : ""}>引用已发布 VLM 场景</option></select></label>
        <div id="nodeVlmSceneFields" ${useScene ? "" : "hidden"}><label><span>已发布 VLM 场景</span><select id="nodeReferencedScene" class="form-select" ${readonly ? "disabled" : ""}>${directSceneOptions(config.referenced_scenario_version_id)}</select></label><p class="muted-copy">引用后复用该场景的模型、提示词和参数；本节点仍可映射前序输出作为上下文。</p></div>
        <div id="nodeVlmCustomFields" ${useScene ? "hidden" : ""}><label><span>VLM 模型</span><select id="nodeVlmModel" class="form-select" ${readonly ? "disabled" : ""}>${state.vlms.filter((item) => item.enabled).map((item) => `<option value="${item.id}" ${Number(config.vlm_model_id) === item.id ? "selected" : ""}>${escapeHtml(item.name)} · ${escapeHtml(item.model_name)}</option>`).join("")}</select></label>${promptTemplateFieldMarkup({ label: "检测提示词", id: "nodePrompt", value: config.prompt || "", placeholder: "输入检测说明，点击 { } 插入变量", ariaLabel: "检测提示词", readonly })}<p class="node-prompt-mapping-tip">变量会以标签显示；点击标签右侧 × 可删除。执行时自动替换为“输入”页签中的参数映射值。</p><div class="form-row"><label><span>温度</span><input id="nodeTemperature" class="form-control" type="number" min="0" max="2" step="0.1" value="${escapeHtml(config.vlm_parameters?.temperature ?? 0)}" ${readonly ? "readonly" : ""}></label><label><span>最大输出 Token</span><input id="nodeMaxTokens" class="form-control" type="number" min="64" max="8192" step="64" value="${escapeHtml(config.vlm_parameters?.max_tokens ?? 1024)}" ${readonly ? "readonly" : ""}></label></div></div>${promptParameterMappingMarkup(config, node, readonly)}${outputMappingMarkup(config, readonly)}`;
    } else if (node.node_type === "VISION_MODEL") {
      const selectedModel = selectedVisionModelVersion(config.model_version_id);
      const profile = visionOutputProfile(selectedModel);
      const classification = profile.supports_classification;
      const taskFields = classification
        ? `<div class="node-model-task-note"><strong>${escapeHtml(visionTaskLabel(selectedModel))}</strong>${visionCapabilityMarkup(profile)}<p>分类节点只输出 <code>predicted_label</code>、Top5 和置信度，不在此节点比较期望类别或输出质量结论。请在规则节点中比较预测类别与开始节点输入。</p></div><label><span>推理置信度参数</span><input id="nodeConfidence" class="form-control" type="number" min="0" max="1" step="0.01" value="${escapeHtml(config.confidence ?? 0.25)}" ${readonly ? "readonly" : ""}></label>`
        : profile.supports_objects
          ? `<div class="node-model-task-note"><strong>${escapeHtml(visionTaskLabel(selectedModel))}</strong>${visionCapabilityMarkup(profile)}<p>${profile.supports_mask ? '分割模型输出目标类别、置信度、bbox 和 mask；流程调试会显示轮廓预览。' : '目标检测只输出目标数量、类别、置信度和 bbox；流程调试会显示画框预览。'}需要质量判定时，再单独连接规则节点。</p></div><div class="form-row"><label><span>置信度阈值</span><input id="nodeConfidence" class="form-control" type="number" min="0" max="1" step="0.01" value="${escapeHtml(config.confidence ?? 0.25)}" ${readonly ? "readonly" : ""}></label><label><span>IoU 阈值</span><input id="nodeIou" class="form-control" type="number" min="0" max="1" step="0.01" value="${escapeHtml(config.iou ?? 0.45)}" ${readonly ? "readonly" : ""}></label></div>`
          : `<div class="node-model-task-note"><strong>${escapeHtml(visionTaskLabel(selectedModel))}</strong>${visionCapabilityMarkup(profile)}<p>请先由该模型适配器声明实际输出字段后，再把特定字段映射给后续节点。</p></div><label><span>置信度阈值</span><input id="nodeConfidence" class="form-control" type="number" min="0" max="1" step="0.01" value="${escapeHtml(config.confidence ?? 0.25)}" ${readonly ? "readonly" : ""}></label>`;
      fields = `<label><span>已发布训练模型版本</span><select id="nodeVisionModelVersion" class="form-select" ${readonly ? "disabled" : ""}>${modelVersionOptions(config.model_version_id)}</select></label>${taskFields}${inputMappingMarkup(config, node, readonly)}${outputMappingMarkup(config, readonly)}`;
    } else if (node.node_type === "IMAGE_CROP") {
      fields = `<div class="node-model-task-note"><strong>单框或多目标裁剪</strong><p>填“目标列表”时，对每个 bbox 裁剪并输出 <code>crops[]</code>，交给循环节点逐项处理；不填时按单个 bbox 裁剪。分割模型同样使用目标 bbox 裁剪，mask 不会作为裁剪边界。</p>${cropBboxSourceHint(node)}</div>${variableFieldMarkup({ label: "输入图片", id: "nodeCropImagePath", value: config.image_path || "{{ input.image_path }}", placeholder: "{{ input.image_path }}", scope: "INPUT", acceptedTypes: "IMAGE", ariaLabel: "输入图片", readonly })}${variableFieldMarkup({ label: "目标列表（批量裁剪）", id: "nodeCropObjects", value: config.objects || "", placeholder: "{{ nodes.model_1.objects }}", scope: "INPUT", acceptedTypes: "OBJECT_LIST", ariaLabel: "批量裁剪目标列表", readonly })}${variableFieldMarkup({ label: "单个裁剪框（bbox）", id: "nodeCropBbox", value: config.bbox || "", placeholder: "{{ nodes.model_1.objects.0.bbox }} 或 [20, 40, 320, 280]", scope: "INPUT", acceptedTypes: "BBOX", ariaLabel: "裁剪框", readonly })}<label><span>边缘扩展比例</span><input id="nodeCropPadding" class="form-control" type="number" min="0" max="1" step="0.01" value="${escapeHtml(config.padding_ratio ?? 0.05)}" ${readonly ? "readonly" : ""}></label>${outputMappingMarkup(config, readonly)}`;
    } else if (node.node_type === "IMAGE_ANNOTATE") {
      fields = `<div class="node-model-task-note"><strong>在检测结果图上画框</strong><p>目标检测可传入全部 <code>objects</code>；分割模型会额外显示轮廓。分类模型没有坐标，不适合本节点。变量选择器会按图片、目标列表和定位框类型过滤可用输出。</p>${annotationSourceHint(node)}</div>${variableFieldMarkup({ label: "输入图片", id: "nodeAnnotateImagePath", value: config.image_path || "{{ input.image_path }}", placeholder: "{{ input.image_path }}", scope: "INPUT", acceptedTypes: "IMAGE", ariaLabel: "画框输入图片", readonly })}${variableFieldMarkup({ label: "目标列表（推荐）", id: "nodeAnnotateObjects", value: config.objects || "", placeholder: "{{ nodes.model_1.objects }}", scope: "INPUT", acceptedTypes: "OBJECT_LIST", ariaLabel: "画框目标列表", readonly })}${variableFieldMarkup({ label: "单个框（可选）", id: "nodeAnnotateBbox", value: config.bbox || "", placeholder: "{{ nodes.model_1.objects.0.bbox }} 或 [20, 40, 320, 280]", scope: "INPUT", acceptedTypes: "BBOX", ariaLabel: "画框单个框", readonly })}<div class="form-row"><label><span>线宽</span><input id="nodeAnnotateLineWidth" class="form-control" type="number" min="1" max="20" step="1" value="${escapeHtml(config.line_width ?? 3)}" ${readonly ? "readonly" : ""}></label><label><span>标签前缀（可选）</span><input id="nodeAnnotateLabelPrefix" class="form-control" value="${escapeHtml(config.label_prefix || "")}" placeholder="例如 检测结果" ${readonly ? "readonly" : ""}></label></div><div class="node-annotate-options"><label><input id="nodeAnnotateShowLabel" type="checkbox" ${config.show_label !== false ? "checked" : ""} ${readonly ? "disabled" : ""}> 显示类别标签</label><label><input id="nodeAnnotateShowConfidence" type="checkbox" ${config.show_confidence !== false ? "checked" : ""} ${readonly ? "disabled" : ""}> 显示置信度</label><label><input id="nodeAnnotateDrawMasks" type="checkbox" ${config.draw_masks !== false ? "checked" : ""} ${readonly ? "disabled" : ""}> 分割模型显示轮廓</label></div>${inputMappingMarkup(config, node, readonly)}${outputMappingMarkup(config, readonly)}`;
    } else if (node.node_type === "RULE" || node.node_type === "IF") {
      const isIf = node.node_type === "IF";
      fields = `${variableFieldMarkup({ label: isIf ? "判断值" : "实际值（模型或上游输出）", id: "nodeActual", value: ruleInputValue(config, "actual"), placeholder: "{{ nodes.model_1.predicted_label }}", scope: "INPUT", ariaLabel: isIf ? "判断值" : "实际值", readonly })}
        <p id="nodeActualSource" class="node-actual-source">${escapeHtml(ruleActualSourceHint(ruleInputValue(config, "actual")))}</p>
        <div class="form-row"><label><span>运算符</span><select id="nodeOperator" class="form-select" ${readonly ? "disabled" : ""}>${ruleOperators.map(([operator, label]) => `<option value="${operator}" ${config.operator === operator ? "selected" : ""}>${label}</option>`).join("")}</select></label>
        <div id="nodeExpectedField" ${config.operator === "EXISTS" ? "hidden" : ""}>${variableFieldMarkup({ label: "期望值（固定值或开始节点参数）", id: "nodeExpected", value: ruleInputValue(config, "expected"), scope: "INPUT", ariaLabel: "期望值", readonly })}</div></div>
        <p class="muted-copy">实际值来自模型或上游节点；期望值可直接填写，也可引用 <code>{{ input.参数键 }}</code>。两者只在此处比较，不限制模型识别的类别。选择“存在”时只判断实际值是否非空。</p>
        ${isIf ? '<p class="muted-copy">条件分支会输出 <code>branch=true/false</code>。选中该节点后，可在下方设置每条连线对应的分支。</p>' : ""}${outputMappingMarkup(config, readonly)}`;
    } else if (node.node_type === "WEB_API") {
      fields = `<div class="form-row"><label><span>请求方法</span><select id="nodeApiMethod" class="form-select" ${readonly ? "disabled" : ""}>${["GET", "POST", "PUT", "PATCH", "DELETE"].map((method) => `<option value="${method}" ${(config.method || "POST") === method ? "selected" : ""}>${method}</option>`).join("")}</select></label><label><span>请求格式</span><select id="nodeApiRequestFormat" class="form-select" ${readonly ? "disabled" : ""}><option value="JSON" ${(config.request_format || "JSON") === "JSON" ? "selected" : ""}>JSON</option><option value="TEXT" ${(config.request_format || "JSON") === "TEXT" ? "selected" : ""}>Text</option></select></label></div>${variableFieldMarkup({ label: "接口地址", id: "nodeApiUrl", value: config.url || "", placeholder: "https://service.example.com/api/check", scope: "INPUT", mode: "INSERT", ariaLabel: "接口地址", readonly })}${variableFieldMarkup({ label: "请求头（JSON）", id: "nodeApiHeaders", value: Object.keys(config.headers || {}).length ? JSON.stringify(config.headers, null, 2) : "", className: "form-control node-textarea", rows: 3, scope: "INPUT", mode: "INSERT", ariaLabel: "请求头", readonly })}${variableFieldMarkup({ label: "请求体（JSON 或文本）", id: "nodeApiBody", value: typeof config.body === "string" ? config.body : JSON.stringify(config.body || {}, null, 2), className: "form-control node-textarea", rows: 5, scope: "INPUT", mode: "INSERT", ariaLabel: "请求体", readonly })}<label><span>响应格式</span><select id="nodeApiResponseFormat" class="form-select" ${readonly ? "disabled" : ""}><option value="JSON" ${(config.response_format || "JSON") === "JSON" ? "selected" : ""}>JSON</option><option value="TEXT" ${(config.response_format || "JSON") === "TEXT" ? "selected" : ""}>Text</option></select></label>${inputMappingMarkup(config, node, readonly)}${outputMappingMarkup(config, readonly)}`;
    } else if (node.node_type === "S3_UPLOAD") {
      fields = `<div class="system-node-note"><strong>按需上传结果图</strong><p>仅当流程经过此节点时上传。桶、前缀和密钥由服务端配置；这里选择待上传图片与可选对象键。建议将图片画框节点的输出作为图片来源。</p></div>${variableFieldMarkup({ label: "待上传图片", id: "nodeS3ImagePath", value: config.image_path || "{{ input.image_path }}", placeholder: "{{ nodes.annotate_1.image_path }}", scope: "INPUT", acceptedTypes: "IMAGE", ariaLabel: "S3 待上传图片", readonly })}${variableFieldMarkup({ label: "对象键（可选）", id: "nodeS3Key", value: config.key || "", placeholder: "{{ input.result_s3_key }} 或 line-L4/part-001_result.jpg", scope: "INPUT", acceptedTypes: "TEXT", ariaLabel: "S3 对象键", readonly })}<p class="muted-copy">外部接口传入的 <code>result_s3_key</code> 优先；都不填时自动按场景、日期和执行 ID 生成唯一 JPEG 路径。上传结果可通过 <code>{{ nodes.s3_1.result_image_path }}</code> 在结束节点返回。</p>${outputMappingMarkup(config, readonly)}`;
    } else if (node.node_type === "ITERATION") {
      fields = `<div class="node-model-task-note"><strong>遍历处理</strong><p>输入数组中的每个对象都会执行一次下方子流程。可在子流程中加入检测、裁剪、判断等节点；不需要把主画布连线拉回。零项返回待确认，超出上限报错。</p></div>
        ${variableFieldMarkup({ label: "输入数组", id: "nodeLoopItems", value: config.items || "", placeholder: "{{ nodes.crop_1.crops }}", scope: "INPUT", acceptedTypes: "JSON|OBJECT_LIST|IMAGE_LIST", ariaLabel: "遍历数组", readonly })}
        <div class="form-row"><label><span>当前项变量名</span><input id="nodeLoopItemName" class="form-control" value="${escapeHtml(config.item_name || "item")}" ${readonly ? "readonly" : ""}></label><label><span>最多处理项数</span><input id="nodeLoopMaxIterations" class="form-control" type="number" min="1" max="100" value="${escapeHtml(config.max_iterations ?? 10)}" ${readonly ? "readonly" : ""}></label></div>
        <label><span>逐项结果汇总</span><select id="nodeLoopBodyAggregate" class="form-select" ${readonly ? "disabled" : ""}><option value="ALL_OK" ${(config.aggregate || "ALL_OK") === "ALL_OK" ? "selected" : ""}>全部 OK 才 OK</option><option value="ANY_OK" ${config.aggregate === "ANY_OK" ? "selected" : ""}>任一 OK 即 OK</option><option value="COLLECT_ONLY" ${config.aggregate === "COLLECT_ONLY" ? "selected" : ""}>只收集结果</option></select></label>
        <div id="nodeLoopBodyHost"><div id="nodeLoopBodyEditor" class="loop-body-editor"></div></div>
        ${parameterSectionMarkup({ id: "nodeLoopBodyOutputParameters", title: "每项子流程输出", description: "留空时取最后节点输出。可引用 {{ nodes.内部节点键.字段 }}。", rows: mappingRows(config.body_output || {}), kind: "MAPPING", readonly, keyPlaceholder: "例如 result", valuePlaceholder: "{{ nodes.body_rule_1.result }}", variableScope: "NONE" })}
        ${inputMappingMarkup(config, node, readonly)}${outputMappingMarkup(config, readonly)}`;
    } else if (node.node_type === "CONDITION_LOOP") {
      const stop = config.stop_condition || {};
      fields = `<div class="node-model-task-note"><strong>条件循环</strong><p>每轮执行内部子流程、更新循环变量，再检查退出条件；满足即退出，否则继续。轮数或时间用尽仍未满足时返回 ERROR，不会误判 OK。</p></div>
        ${parameterSectionMarkup({ id: "nodeLoopInitialState", title: "初始循环变量", description: "输入变量名和值；数字、布尔值和数组可输入 JSON。内部节点通过 {{ state.变量名 }} 引用。", rows: mappingRows(config.initial_state || {}), kind: "MAPPING", readonly, keyPlaceholder: "例如 count", valuePlaceholder: "0", variableScope: "INPUT" })}
        <div class="form-row"><label><span>最大轮数（1–20）</span><input id="nodeLoopMaxIterations" class="form-control" type="number" min="1" max="20" value="${escapeHtml(config.max_iterations ?? 3)}" ${readonly ? "readonly" : ""}></label><label><span>最长执行秒数（≤300）</span><input id="nodeLoopTimeout" class="form-control" type="number" min="1" max="300" value="${escapeHtml(config.timeout_seconds ?? 30)}" ${readonly ? "readonly" : ""}></label></div>
        <div id="nodeLoopBodyHost"><div id="nodeLoopBodyEditor" class="loop-body-editor"></div></div>
        ${conditionUpdatesMarkup(config.state_updates || {}, readonly)}
        <section class="node-parameter-section"><div class="node-parameter-heading"><div><strong>退出条件</strong><small>每轮更新变量后检查；可用 {{ state.count }}、{{ nodes.内部节点键.字段 }} 或 {{ round.number }}。</small></div></div>
          ${variableFieldMarkup({ label: "实际值", id: "nodeLoopStopActual", value: stop.actual || "", placeholder: "{{ state.count }}", scope: "INPUT", ariaLabel: "退出条件实际值", readonly })}
          <div class="form-row"><label><span>比较方式</span><select id="nodeLoopStopOperator" class="form-select" ${readonly ? "disabled" : ""}>${ruleOperators.map(([operator, label]) => `<option value="${operator}" ${stop.operator === operator ? "selected" : ""}>${label}</option>`).join("")}</select></label>${variableFieldMarkup({ label: "期望值", id: "nodeLoopStopExpected", value: stop.expected ?? "", placeholder: "3", scope: "INPUT", ariaLabel: "退出条件期望值", readonly })}</div></section>
        ${parameterSectionMarkup({ id: "nodeLoopBodyOutputParameters", title: "每轮子流程输出", description: "留空时取最后节点输出；可引用内部节点字段。", rows: mappingRows(config.body_output || {}), kind: "MAPPING", readonly, keyPlaceholder: "例如 result", valuePlaceholder: "{{ nodes.body_rule_1.result }}", variableScope: "NONE" })}
        ${outputMappingMarkup(config, readonly)}`;
    } else if (node.node_type === "LOOP") {
      const loopVlm = config.operation === "VLM";
      fields = `<div class="node-model-task-note"><strong>逐张裁剪图执行检测</strong><p>推荐连接：目标检测 / 分割 → 图片裁剪（目标列表）→ 循环控制（逐项 VLM）→ 图片画框 → S3 上传 → 结束。循环不需要把连线绕回节点；节点内部逐项调用。旧流程可保留“仅整理集合”。</p></div><label><span>执行方式</span><select id="nodeLoopOperation" class="form-select" ${readonly ? "disabled" : ""}><option value="VLM" ${loopVlm ? "selected" : ""}>逐项 VLM 检测</option><option value="PASS_THROUGH" ${loopVlm ? "" : "selected"}>仅整理集合（旧版兼容）</option></select></label>${variableFieldMarkup({ label: "循环集合", id: "nodeLoopItems", value: config.items || "", placeholder: "{{ nodes.crop_1.crops }}", scope: "INPUT", acceptedTypes: loopVlm ? "IMAGE_LIST|JSON" : "JSON|OBJECT_LIST|IMAGE_LIST", ariaLabel: "循环集合", readonly })}<div class="form-row"><label><span>当前项变量名</span><input id="nodeLoopItemName" class="form-control" value="${escapeHtml(config.item_name || "item")}" ${readonly ? "readonly" : ""}></label><label><span>最大次数</span><input id="nodeLoopMaxIterations" class="form-control" type="number" min="1" max="${loopVlm ? 30 : 1000}" value="${escapeHtml(config.max_iterations ?? 10)}" ${readonly ? "readonly" : ""}></label></div><div id="nodeLoopVlmFields" ${loopVlm ? "" : "hidden"}><label><span>逐项检测 VLM</span><select id="nodeLoopVlmModel" class="form-select" ${readonly ? "disabled" : ""}><option value="">请选择模型</option>${state.vlms.filter((item) => item.enabled).map((item) => `<option value="${item.id}" ${Number(config.vlm_model_id) === item.id ? "selected" : ""}>${escapeHtml(item.name)} · ${escapeHtml(item.model_name)}</option>`).join("")}</select></label>${promptTemplateFieldMarkup({ label: "逐项检测提示词", id: "nodeLoopPrompt", value: config.prompt || "", placeholder: "检查当前裁剪图中的 {{ item.label }}", ariaLabel: "逐项检测提示词", readonly })}<div class="form-row"><label><span>单次并发数（最多 3）</span><input id="nodeLoopParallelism" class="form-control" type="number" min="1" max="3" value="${escapeHtml(config.max_parallelism ?? 3)}" ${readonly ? "readonly" : ""}></label><label><span>结果汇总</span><select id="nodeLoopAggregate" class="form-select" ${readonly ? "disabled" : ""}><option value="ALL_OK" ${(config.aggregate || "ALL_OK") === "ALL_OK" ? "selected" : ""}>全部 OK 才 OK</option><option value="ANY_OK" ${config.aggregate === "ANY_OK" ? "selected" : ""}>任一 OK 即 OK</option><option value="COLLECT_ONLY" ${config.aggregate === "COLLECT_ONLY" ? "selected" : ""}>只收集结果，不做总判定</option></select></label></div><div class="form-row"><label><span>温度</span><input id="nodeLoopTemperature" class="form-control" type="number" min="0" max="2" step="0.1" value="${escapeHtml(config.vlm_parameters?.temperature ?? 0)}" ${readonly ? "readonly" : ""}></label><label><span>最大输出 Token</span><input id="nodeLoopMaxTokens" class="form-control" type="number" min="64" max="8192" step="64" value="${escapeHtml(config.vlm_parameters?.max_tokens ?? 1024)}" ${readonly ? "readonly" : ""}></label></div><p class="muted-copy">超过最大次数会报错，不会漏掉目标后判 OK。VLM 报错会保留在对应项并使总结果 ERROR；零目标为 UNCERTAIN。每项可在调试追踪中查看耗时与原始结果。</p></div>${inputMappingMarkup(config, node, readonly)}${outputMappingMarkup(config, readonly)}`;
      fields = fields.replace("逐张裁剪图执行检测", "逐项执行循环体").replace("推荐连接：目标检测 / 分割 → 图片裁剪（目标列表）→ 循环控制（逐项 VLM）→ 图片画框 → S3 上传 → 结束。循环不需要把连线绕回节点；节点内部逐项调用。旧流程可保留“仅整理集合”。", "循环内部可以按顺序添加多个处理节点；开始和结束由系统自动连接。旧版逐项 VLM 和仅整理集合继续兼容。");
      fields = fields.replace('<option value="VLM"', `<option value="SUBFLOW" ${config.operation === "SUBFLOW" ? "selected" : ""}>自定义循环体</option><option value="VLM"`);
      if (config.operation === "SUBFLOW") fields = fields.replace('<option value="PASS_THROUGH" selected', '<option value="PASS_THROUGH"');
      fields += `<div id="nodeLoopSubflowFields" ${config.operation === "SUBFLOW" ? "" : "hidden"}><label><span>结果汇总</span><select id="nodeLoopBodyAggregate" class="form-select" ${readonly ? "disabled" : ""}><option value="ALL_OK" ${(config.aggregate || "ALL_OK") === "ALL_OK" ? "selected" : ""}>全部 OK 才 OK</option><option value="ANY_OK" ${config.aggregate === "ANY_OK" ? "selected" : ""}>任一 OK 即 OK</option><option value="COLLECT_ONLY" ${config.aggregate === "COLLECT_ONLY" ? "selected" : ""}>只收集节点输出</option></select></label><div id="nodeLoopBodyHost"><div id="nodeLoopBodyEditor" class="loop-body-editor"></div></div>${parameterSectionMarkup({ id: "nodeLoopBodyOutputParameters", title: "循环体每项输出", description: "可选；留空时返回循环结束前最后节点的结果。节点字段可填写 {{ nodes.子节点键.输出字段 }}。", rows: mappingRows(config.body_output || {}), kind: "MAPPING", readonly, keyPlaceholder: "例如 result", valuePlaceholder: "{{ nodes.body_rule_1.result }}", variableScope: "NONE" })}</div>`;
    } else {
      fields = node.node_type === "START"
        ? `<div class="system-node-note"><strong>开始节点</strong><p>系统自动注入 <code>{{ input.image_path }}</code>（配方调用时为 ROI 图片）、<code>{{ input.original_image_path }}</code>（本地原始整图）和 <code>{{ input.source_image_path }}</code>（调用方原图路径）。配方调用还提供 <code>{{ input.roi_bbox }}</code>（ROI 在原图中的坐标）；画框到原图时须先转换 ROI 内坐标。外部场景接口仍只传顶层 <code>image_path</code>，此时两个图片变量指向同一张图。</p></div>${parameterSectionMarkup({ id: "nodeStartInputParameters", title: "流程输入参数", description: "这里只维护业务校验参数；图片和 ROI 坐标由系统传入，不要重复添加。", rows: schemaRows(config.inputs || []), kind: "SCHEMA", readonly, keyLabel: "参数键", valueLabel: "参数名称", keyPlaceholder: "例如 ocr_text", valuePlaceholder: "例如 OCR 校验文字", variableScope: "NONE" })}`
        : `<div class="system-node-note"><strong>结束节点</strong><p>未配置返回参数时，系统默认返回最后一个执行节点的完整结果。</p></div>${parameterSectionMarkup({ id: "nodeEndOutputParameters", title: "对外返回参数", description: "点击参数值右侧 { }，从任意上游节点输出中选择返回字段。", rows: mappingRows(config.output || {}), readonly, keyPlaceholder: "例如 result", valuePlaceholder: "例如 {{ nodes.vlm_1.result }}", variableScope: "INPUT" })}`;
    }
    const outgoing = (currentVersion()?.edges || []).filter((edge) => edge.source_node_key === node.node_key);
    const branchMapping = node.node_type === "IF" && outgoing.length ? `<section class="edge-condition-editor"><strong>分支连线</strong>${outgoing.map((edge) => `<label><span>→ ${escapeHtml(nodeByKey(edge.target_node_key)?.name || edge.target_node_key)}</span><select class="form-select form-select-sm" data-edge-branch="${edge.id}" ${readonly ? "disabled" : ""}><option value="">默认</option><option value="true" ${edge.mapping_json?.branch === "true" ? "selected" : ""}>条件成立（true）</option><option value="false" ${edge.mapping_json?.branch === "false" ? "selected" : ""}>条件不成立（false）</option></select></label>`).join("")}</section>` : "";
    const save = readonly ? "" : `<div class="inspector-actions"><button class="btn btn-primary" id="saveNodeConfig" type="button">保存节点</button>${["START", "END"].includes(node.node_type) ? "" : `<button class="btn btn-outline-danger" id="deleteNode" type="button">删除节点</button>`}</div>`;
    target.innerHTML = `<div class="node-config-shell">
      <header class="node-config-summary">
        <span class="node-config-summary-icon node-config-type-${escapeHtml(String(node.node_type || "").toLowerCase())}" aria-hidden="true">${nodeTypeIcon(node.node_type)}</span>
        <div><small>${escapeHtml(nodeTypeLabel(node.node_type))}</small><strong>${escapeHtml(node.name)}</strong><code>${escapeHtml(node.node_key)}</code></div>
      </header>
      <nav class="node-config-tabs" role="tablist" aria-label="节点配置页签"></nav>
      <div class="node-config-panels">
        <section class="node-config-panel" data-inspector-panel="SETTINGS" role="tabpanel">
          <div class="node-config-section"><header><strong>节点设置</strong><small>配置节点本身的执行方式和业务规则。</small></header><div id="workflowNodeSettingsContent" class="stack-form compact-stack-form">${identity}${fields}${branchMapping}</div></div>
        </section>
        <section class="node-config-panel" data-inspector-panel="INPUT" role="tabpanel"><div class="node-config-section"><header><strong>节点输入与参数</strong><small>选择模型、规则值或上游变量，配置本节点执行方式。</small></header><div id="workflowNodeInputContent" class="stack-form compact-stack-form"></div></div></section>
        <section class="node-config-panel" data-inspector-panel="OUTPUT" role="tabpanel"><div class="node-config-section"><header><strong>输出声明</strong><small>为节点原始输出设置易读字段，供后续节点或接口返回使用。</small></header><div id="workflowNodeOutputContent" class="stack-form compact-stack-form"></div></div></section>
        <section class="node-config-panel" data-inspector-panel="CONTRACT" role="tabpanel"><div class="node-config-section"><header><strong>节点契约</strong><small>查看节点可接收和可输出的数据类型；变量选择器会据此过滤不兼容字段。</small></header><div id="workflowNodeContractContent" class="stack-form compact-stack-form"></div></div></section>
      </div>
      ${save}
    </div>`;
    setupInspectorTabs(target);
    if (loopTypes.has(node.node_type)) {
      if (node.node_type === "LOOP") byId("nodeLoopMaxIterations").max = config.operation === "VLM" ? "30" : config.operation === "SUBFLOW" ? "100" : "1000";
      renderLoopBodyEditor();
    }
  }

  function renderWorkflowDesigner() {
    const version = currentVersion();
    if (!version) return;
    const workflow = state.scene.mode === "WORKFLOW";
    byId("workflowDesigner").hidden = !workflow;
    byId("directDesigner").hidden = workflow;
    if (!workflow) return;
    if (!nodeById(state.selectedNodeId)) state.selectedNodeId = null;
    renderWorkflowCanvas();
    renderInspector();
    updateWorkflowWorkbenchPanels();
  }

  function renderAll() {
    updateHeader();
    renderDirectDesigner();
    renderWorkflowDesigner();
  }

  async function loadData({ preserveVersion = true } = {}) {
    closeSubflowCanvas();
    const [scene, vlms, publishedScenes, visionVersions] = await Promise.all([
      request(`${api}/scenarios/${sceneId}`), request(`${api}/vlm-models`), request(`${api}/scenarios/published`), request(`${api}/vision-models/published`),
    ]);
    state.scene = scene;
    state.vlms = vlms;
    state.publishedScenes = publishedScenes;
    state.visionVersions = visionVersions;
    if (!preserveVersion || !scene.versions.some((version) => version.id === state.versionId)) {
      state.versionId = scene.versions.find((version) => version.status === "DRAFT")?.id || scene.versions[0]?.id || null;
      state.selectedEdgeId = null;
    }
    renderAll();
  }

  function directPayload() {
    const version = currentVersion();
    const extra = parseJson(byId("directExtraParams").value, "高级参数", {});
    return {
      primary_vlm_model_id: Number(byId("directPrimaryVlm").value) || null,
      review_vlm_model_id: Number(byId("directReviewVlm").value) || null,
      prompt_template: byId("directPrompt").value.trim() || null,
      definition_json: {
        ...(version.definition_json || {}),
        vlm_parameters: {
          ...extra,
          temperature: Number(byId("directTemperature").value),
          max_tokens: Number(byId("directMaxTokens").value),
          thinking_enabled: byId("directThinking").checked,
        },
      },
      input_schema_json: { fields: readSchemaParameterRows("directInputParameters", "ROI 可传入参数") },
    };
  }
  async function saveDirect({ quiet = false } = {}) {
    if (!isDraft()) { notify("已发布版本不能直接修改，请先新建草稿版本。", "warning"); return false; }
    try {
      const saved = await request(`${api}/scenarios/versions/${currentVersion().id}`, {
        method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(directPayload()),
      });
      state.scene.versions = state.scene.versions.map((item) => item.id === saved.id ? saved : item);
      renderAll();
      if (!quiet) notify("VLM 场景草稿已保存。");
      return true;
    } catch (error) { notify(error.message, "danger"); return false; }
  }
  async function saveDesigner() {
    if (state.scene.mode === "VLM_DIRECT") return saveDirect();
    if (!isDraft()) { notify("已发布版本不能直接修改，请先新建草稿版本。", "warning"); return false; }
    if (!nodeById(state.selectedNodeId)) {
      notify("请先在画布中选择需要保存的节点。", "info");
      return false;
    }
    return saveNodeConfig();
  }

  function nextDraftVersionName(source) {
    const prefix = source?.version ? `${source.version}-draft` : "draft";
    const existing = new Set((state.scene?.versions || []).map((item) => String(item.version || "").toLowerCase()));
    let sequence = 1;
    let candidate = `${prefix}-${sequence}`;
    while (existing.has(candidate.toLowerCase())) {
      sequence += 1;
      candidate = `${prefix}-${sequence}`;
    }
    return candidate;
  }

  async function cloneVersion() {
    const source = currentVersion();
    if (!source) { notify("当前场景没有可复制的版本。", "warning"); return; }
    const version = nextDraftVersionName(source);
    const button = byId("designerClone");
    const originalText = button.textContent;
    button.disabled = true;
    button.textContent = "正在创建草稿…";
    try {
      const created = await request(`${api}/scenarios/${state.scene.id}/versions`, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({
          version, source_version_id: source.id, prompt_template: source.prompt_template, primary_vlm_model_id: source.primary_vlm_model_id, review_vlm_model_id: source.review_vlm_model_id, input_schema_json: source.input_schema_json || {}, output_schema_json: source.output_schema_json || {}, definition_json: source.definition_json || {},
        }),
      });
      state.versionId = created.id;
      state.selectedNodeId = null;
      await loadData();
      notify(`已创建草稿版本 V${created.version}，现在可以编辑节点与提示词。`);
    } catch (error) { notify(error.message, "danger"); }
    finally {
      button.disabled = false;
      button.textContent = originalText;
    }
  }
  async function publishVersion() {
    if (!isDraft()) return;
    const preflight = await runDesignerPreflight({ openModal: false, quiet: true });
    if (!preflight?.ready) {
      if (preflight) {
        const modal = window.bootstrap?.Modal?.getOrCreateInstance(byId("workflowPreflightModal"));
        modal?.show();
      }
      notify("发布前检查未通过，请先处理标记的问题。", "warning");
      return;
    }
    if (!confirm("发布后该版本可被工艺配方 ROI 引用；当前已发布版本会归档。确认发布吗？")) return;
    try {
      const published = await request(`${api}/scenarios/versions/${currentVersion().id}/publish`, { method: "POST" });
      await loadData();
      notify(`场景版本已发布。外部调用地址：${published.api_endpoint_path || "已生成"}`);
    } catch (error) { notify(error.message, "danger"); }
  }

  function previewSelectedImage(file) {
    if (state.previewUrl) URL.revokeObjectURL(state.previewUrl);
    state.previewUrl = file ? URL.createObjectURL(file) : null;
    byId("directRunTest").disabled = !file;
    byId("directTestPreview").innerHTML = file && state.previewUrl ? `<img src="${state.previewUrl}" alt="测试图片预览"><div><strong>${escapeHtml(file.name)}</strong><span>${Math.round(file.size / 1024)} KB</span></div>` : '<div class="empty-state"><span>▧</span><strong>选择测试图片</strong><p>图片不会修改场景或生产数据，只用于当前草稿调试。</p></div>';
  }
  function clearDirectConversation() {
    const conversation = byId("directConversation");
    conversation.innerHTML = '<div class="empty-state compact-empty"><span>◌</span><strong>等待测试</strong><p>上传图片并开始检测后，这里仅展示本次测试的结果。</p></div>';
    conversation.scrollTop = 0;
    byId("directClearResult").disabled = true;
  }
  function startDirectConversation() {
    const conversation = byId("directConversation");
    conversation.replaceChildren();
    conversation.scrollTop = 0;
    byId("directClearResult").disabled = false;
  }
  function directResultSummary(result, output) {
    const detail = output && typeof output === "object" ? output : {};
    const displayResult = detail.result ?? result.result ?? "UNCERTAIN";
    const confidence = detail.confidence ?? detail.score ?? result.score;
    const actual = detail.actual ?? detail.checked_text ?? detail.value;
    const expected = detail.expected ?? detail.expected_text;
    const reason = detail.reason ?? detail.message ?? "模型未返回检测说明。";
    return [
      `检测结果：${displayResult}`,
      confidence !== null && confidence !== undefined && confidence !== "" ? `置信度：${confidence}` : null,
      expected !== null && expected !== undefined && expected !== "" ? `期望值：${expected}` : null,
      actual !== null && actual !== undefined && actual !== "" ? `实际值：${actual}` : null,
      `检测说明：${reason}`,
      result.elapsed_ms !== null && result.elapsed_ms !== undefined ? `处理耗时：${result.elapsed_ms} ms` : null,
    ].filter(Boolean).join("\n");
  }
  function appendConversation(role, title, content, raw = null) {
    const message = document.createElement("article");
    message.className = `chat-message ${role}`;
    const body = typeof content === "string" ? `<p>${escapeHtml(content)}</p>` : `<pre>${escapeHtml(JSON.stringify(content, null, 2))}</pre>`;
    message.innerHTML = `<small>${escapeHtml(title)}</small>${body}${raw ? `<details><summary>查看完整结果</summary><pre>${escapeHtml(JSON.stringify(raw, null, 2))}</pre></details>` : ""}`;
    byId("directConversation").appendChild(message);
    byId("directConversation").scrollTop = byId("directConversation").scrollHeight;
  }
  async function runDirectTest() {
    const file = byId("directTestImage").files?.[0];
    if (!file) { notify("请先上传测试图片。", "warning"); return; }
    let context;
    try {
      context = directTestContext();
    } catch (error) {
      notify(error.message, "warning");
      return;
    }
    // Testing a published version must be read-only.  A draft is saved first
    // so the uploaded image always executes exactly what the operator sees.
    if (isDraft() && !await saveDirect({ quiet: true })) return;
    const form = new FormData(); form.append("image", file);
    form.append("context_json", JSON.stringify(context));
    startDirectConversation();
    const modeText = isDraft() ? "提示词已按左侧草稿保存。" : "正在测试当前已发布版本，配置不会被修改。";
    appendConversation("user", "当前测试", `图片：${file.name}\n${modeText}${Object.keys(context).length ? `\n测试参数：${JSON.stringify(context)}` : ""}`);
    appendConversation("assistant pending", "VLM", "正在检测图片…");
    try {
      const result = await request(`${api}/scenarios/versions/${currentVersion().id}/preview-upload`, { method: "POST", body: form });
      const pending = byId("directConversation").querySelector(".pending");
      if (pending) pending.remove();
      const output = result.output_json?.result || result.output_json || {};
      appendConversation("assistant", `${result.result || "UNCERTAIN"} · ${result.score ?? "—"}`, directResultSummary(result, output), result);
    } catch (error) {
      const pending = byId("directConversation").querySelector(".pending");
      if (pending) pending.remove();
      appendConversation("assistant error", "检测失败", error.message);
      notify(error.message, "danger");
    }
  }

  function previewWorkflowTestImage(file) {
    if (state.workflowTestPreviewUrl) URL.revokeObjectURL(state.workflowTestPreviewUrl);
    state.workflowTestPreviewUrl = file ? URL.createObjectURL(file) : null;
    byId("workflowRunTest").disabled = !file;
    byId("workflowTestRunPanel").disabled = !file;
    byId("workflowTestPreview").innerHTML = file && state.workflowTestPreviewUrl
      ? `<img src="${state.workflowTestPreviewUrl}" alt="流程测试图片预览"><div><strong>${escapeHtml(file.name)}</strong><span>${Math.round(file.size / 1024)} KB</span></div>`
      : '<div class="empty-state compact-empty"><span>▧</span><strong>选择测试图片</strong><p>图片仅用于当前草稿调试，不会写入生产记录。</p></div>';
  }

  function formatWorkflowTraceValue(value) {
    try { return JSON.stringify(value ?? {}, null, 2); } catch { return String(value ?? ""); }
  }

  function workflowTraceStatus(status) {
    const normalized = String(status || "UNCERTAIN").toUpperCase();
    if (["OK", "COMPLETED"].includes(normalized)) return "success";
    if (["NG", "ERROR"].includes(normalized)) return "danger";
    if (normalized === "SKIPPED") return "muted";
    return "warning";
  }

  function workflowResultLabel(result) {
    return String(result || "UNCERTAIN").toUpperCase();
  }

  function workflowResultBadgeClass(result) {
    const status = workflowResultLabel(result);
    if (status === "OK") return "success";
    if (["NG", "ERROR"].includes(status)) return "danger";
    if (status === "SKIPPED") return "muted";
    return "warning";
  }

  function setWorkflowTestRunMeta({ result = "未运行", elapsed = null, traceCount = 0 } = {}) {
    const badge = byId("workflowTestResultBadge");
    const label = result === "未运行" || result === "运行中" ? result : workflowResultLabel(result);
    badge.textContent = label;
    badge.className = `workflow-test-result-badge ${result === "运行中" ? "running" : result === "未运行" ? "muted" : workflowResultBadgeClass(label)}`;
    byId("workflowTestElapsed").textContent = elapsed === null || elapsed === undefined ? "— ms" : `${elapsed} ms`;
    byId("workflowTestTraceCount").textContent = typeof traceCount === "number" ? `${traceCount} 个节点` : String(traceCount);
  }

  function workflowFinalOutputMarkup(result, finalOutput, errorMessage = "", traces = []) {
    const label = workflowResultLabel(result || finalOutput?.result);
    const latestModelTrace = [...traces].reverse().find((trace) => trace.node_type === "VISION_MODEL" && trace.output);
    const latestAnnotationTrace = [...traces].reverse().find((trace) => trace.node_type === "IMAGE_ANNOTATE" && trace.output?.image_url);
    const detection = finalOutput?.task_type === "YOLO_DETECTION" || finalOutput?.task_type === "YOLO_SEGMENTATION"
      ? finalOutput : (finalOutput?.res?.task_type ? finalOutput.res : latestModelTrace?.output);
    const detectionCount = detection?.object_count ?? detection?.detection_count ?? finalOutput?.annotation_count ?? latestAnnotationTrace?.output?.annotation_count;
    const reason = finalOutput?.reason || finalOutput?.message || errorMessage
      || (detectionCount !== null && detectionCount !== undefined && label === "UNCERTAIN"
        ? "模型已完成目标预测；流程尚未进行质量判定。" : "流程已完成，可查看完整返回内容。");
    const classification = finalOutput?.classification || finalOutput?.res?.classification || latestModelTrace?.output?.classification;
    const predictedLabel = finalOutput?.predicted_label || finalOutput?.res?.predicted_label || latestModelTrace?.output?.predicted_label || classification?.top1_label;
    const predictionConfidence = classification?.top1_confidence ?? finalOutput?.res?.confidence ?? finalOutput?.confidence;
    const confidenceText = predictionConfidence !== null && predictionConfidence !== undefined && Number.isFinite(Number(predictionConfidence))
      ? ` · 置信度 ${(Number(predictionConfidence) * 100).toFixed(1)}%`
      : "";
    const prediction = predictedLabel
      ? `<div class="workflow-classification-summary"><strong>模型预测类别：${escapeHtml(predictedLabel)}</strong><span>${escapeHtml(confidenceText)}</span></div>`
      : "";
    const detectionSummary = detectionCount !== null && detectionCount !== undefined
      ? `<div class="workflow-classification-summary"><strong>检测目标：${escapeHtml(detectionCount)} 个</strong><span>每个目标的类别、置信度和 bbox 见节点输出</span></div>` : "";
    const artifact = workflowTraceArtifactMarkup(finalOutput) || workflowTraceArtifactMarkup(latestAnnotationTrace?.output) || workflowTraceArtifactMarkup(detection);
    return `<div class="workflow-final-result-summary ${workflowResultBadgeClass(label)}"><span>${escapeHtml(label)}</span><p>${escapeHtml(reason)}</p></div>${prediction}${detectionSummary}${artifact}<pre>${escapeHtml(formatWorkflowTraceValue(finalOutput))}</pre>`;
  }

  function workflowTraceArtifactMarkup(output) {
    const entries = Array.isArray(output?.crops) ? output.crops : output?.aggregate && Array.isArray(output?.items) ? output.items : null;
    if (entries) {
      if (!entries.length) return '<p class="muted-copy">没有可展示的目标裁剪图。</p>';
      return `<div class="workflow-crop-gallery">${entries.map((item, index) => {
        const imageUrl = String(item?.image_url || item?.item?.image_url || "").trim();
        if (!imageUrl.startsWith("/files/")) return "";
        const verdict = item.result ? ` · ${item.result}` : "";
        return `<a href="${escapeHtml(imageUrl)}" target="_blank" rel="noopener"><img src="${escapeHtml(imageUrl)}?v=${Date.now()}" alt="第 ${index + 1} 个裁剪目标"><span>#${index + 1} ${escapeHtml(item.label || item.item?.label || "目标")}${escapeHtml(verdict)}${item.elapsed_ms !== undefined ? ` · ${escapeHtml(item.elapsed_ms)} ms` : ""}</span></a>`;
      }).join("")}</div>`;
    }
    const preview = Boolean(output?.preview_image_url);
    const imageUrl = String(output?.preview_image_url || output?.image_url || "").trim();
    if (!imageUrl.startsWith("/files/")) return "";
    return `<a class="workflow-trace-artifact ${preview ? "is-model-preview" : ""}" href="${escapeHtml(imageUrl)}" target="_blank" rel="noopener"><img src="${escapeHtml(imageUrl)}?v=${Date.now()}" alt="${preview ? "模型画框预览" : "节点图片输出"}"><span>${preview ? "查看完整画框结果图 ↗" : "查看节点图片输出 ↗"}</span></a>`;
  }

  function workflowLoopTraceMarkup(output) {
    const rounds = Array.isArray(output?.rounds);
    const items = rounds ? output.rounds : Array.isArray(output?.items) ? output.items : [];
    if (!items.some((item) => Array.isArray(item?.traces))) return "";
    return `<section class="workflow-loop-traces"><header><small>${rounds ? "条件循环逐轮执行" : "遍历处理逐项执行"}</small><span>${items.length} ${rounds ? "轮" : "项"}</span></header>${items.map((item, index) => `<details class="workflow-loop-item"><summary><strong>#${index + 1} ${escapeHtml(rounds ? "循环轮次" : item.item?.label || item.item?.value || "当前项")}</strong><span class="workflow-trace-status ${workflowTraceStatus(item.result)}">${escapeHtml(item.result || "UNCERTAIN")}</span><small>${escapeHtml(item.elapsed_ms ?? 0)} ms</small></summary><div>${rounds ? `<small>退出条件：${escapeHtml(formatWorkflowTraceValue(item.stop_condition || {}))}</small>` : ""}${(item.traces || []).map((child) => `<details class="workflow-loop-step"><summary>${escapeHtml(child.node_name || child.node_key)} · ${escapeHtml(child.elapsed_ms ?? 0)} ms</summary>${workflowTraceArtifactMarkup(child.output)}<small>输入</small><pre>${escapeHtml(formatWorkflowTraceValue(child.input))}</pre><small>输出</small><pre>${escapeHtml(formatWorkflowTraceValue(child.output))}</pre></details>`).join("")}</div></details>`).join("")}</section>`;
  }

  function renderWorkflowTestResult(execution) {
    const panel = byId("workflowTestPanel");
    const stage = document.querySelector(".workflow-stage-panel");
    const traces = execution.output_json?.traces || [];
    const finalOutput = execution.output_json?.result || {};
    const result = execution.result || finalOutput.result || "UNCERTAIN";
    panel.hidden = false;
    stage?.classList.add("has-test");
    byId("workflowTestSummary").textContent = `最终结果：${result} · 总耗时：${execution.elapsed_ms ?? "—"} ms · 已执行 ${traces.length} 个节点`;
    setWorkflowTestRunMeta({ result, elapsed: execution.elapsed_ms, traceCount: traces.length });
    byId("workflowTestFinalOutput").innerHTML = workflowFinalOutputMarkup(result, finalOutput, execution.error_message || "", traces);
    byId("workflowTestTraceList").innerHTML = traces.length
      ? traces.map((trace, index) => `<details class="workflow-trace-card" ${index === traces.length - 1 ? "open" : ""}><summary><div><span class="workflow-trace-index">${index + 1}</span><div><strong>${escapeHtml(trace.node_name || trace.node_key || "节点")}</strong><small>${escapeHtml(nodeTypeLabel(trace.node_type || ""))} · ${escapeHtml(trace.node_key || "")}</small></div></div><div class="workflow-trace-summary-meta"><span class="workflow-trace-status ${workflowTraceStatus(trace.status)}">${escapeHtml(trace.status || "UNCERTAIN")}</span><b>${escapeHtml(trace.elapsed_ms ?? 0)} ms</b><i>⌄</i></div></summary><div class="workflow-trace-detail">${workflowTraceArtifactMarkup(trace.output)}${workflowLoopTraceMarkup(trace.output)}<section><header><small>节点输入</small><span>完整参数</span></header><pre>${escapeHtml(formatWorkflowTraceValue(trace.input))}</pre></section><section><header><small>节点输出</small><span>完整结果</span></header><pre>${escapeHtml(formatWorkflowTraceValue(trace.output))}</pre></section></div></details>`).join("")
      : `<div class="empty-state compact-empty"><span>!</span><strong>流程未产生节点追踪</strong><p>${escapeHtml(execution.error_message || "请检查节点配置和连接关系。")}</p><pre>${escapeHtml(formatWorkflowTraceValue(finalOutput))}</pre></div>`;
    panel.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  function startWorkflowTestRun() {
    const panel = byId("workflowTestPanel");
    panel.hidden = false;
    document.querySelector(".workflow-stage-panel")?.classList.add("has-test");
    setWorkbenchPanel("inspector", true);
    setWorkbenchPanel("palette", true);
    setWorkflowTestRunMeta({ result: "运行中", traceCount: "执行中" });
    byId("workflowTestSummary").textContent = "正在按编排顺序运行节点；完成后可展开每个节点查看实际输入、输出和耗时。";
    byId("workflowTestTraceList").innerHTML = '<div class="workflow-test-pending"><span></span><div><strong>正在运行流程</strong><p>等待各节点完成执行与变量解析…</p></div></div>';
    byId("workflowTestFinalOutput").innerHTML = '<div class="workflow-test-pending"><span></span><div><strong>正在等待最终输出</strong><p>结束节点返回后会在这里展示结果。</p></div></div>';
  }

  function clearWorkflowTest() {
    const panel = byId("workflowTestPanel");
    panel.hidden = true;
    document.querySelector(".workflow-stage-panel")?.classList.remove("has-test");
    byId("workflowTestTraceList").innerHTML = '<div class="empty-state compact-empty"><span>⌘</span><strong>等待流程测试</strong><p>结果会按执行顺序展示每个节点的输入、输出和耗时。</p></div>';
    byId("workflowTestFinalOutput").innerHTML = '<div class="empty-state compact-empty"><span>✓</span><strong>等待最终结果</strong><p>运行流程后，这里会展示最终状态和完整返回内容。</p></div>';
    byId("workflowTestSummary").textContent = "上传测试图后，查看每个节点的输入、输出与处理耗时。";
    setWorkflowTestRunMeta();
  }

  async function runWorkflowTest() {
    const file = byId("workflowTestImage").files?.[0];
    if (!file) { notify("请先选择流程测试图片。", "warning"); return; }
    let context;
    try {
      context = parseJson(byId("workflowTestContext").value, "测试参数", {});
      if (!context || Array.isArray(context) || typeof context !== "object") throw new Error("测试参数必须是 JSON 对象。");
    } catch (error) { notify(error.message, "danger"); return; }
    const button = byId("workflowRunTest");
    const originalText = button.textContent;
    button.disabled = true;
    button.textContent = "测试中…";
    byId("workflowTestRunPanel").disabled = true;
    startWorkflowTestRun();
    const form = new FormData();
    form.append("image", file);
    form.append("context_json", JSON.stringify(context));
    try {
      const result = await request(`${api}/scenarios/versions/${currentVersion().id}/preview-upload`, { method: "POST", body: form });
      renderWorkflowTestResult(result);
      notify(`流程测试完成：${result.result || "UNCERTAIN"}。`);
    } catch (error) {
      renderWorkflowTestResult({ result: "ERROR", elapsed_ms: 0, error_message: error.message, output_json: { traces: [], result: { result: "ERROR", reason: error.message } } });
      notify(error.message, "danger");
    } finally {
      button.disabled = !file;
      button.textContent = originalText;
      byId("workflowTestRunPanel").disabled = !file;
    }
  }

  async function createWorkflowNode(type, event) {
    if (!isDraft()) { notify("已发布版本不能修改，请先新建草稿版本。", "warning"); return; }
    const position = canvasPoint(event);
    const versionId = currentVersion().id;
    for (let attempt = 0; attempt < 2; attempt += 1) {
      const nodeKey = nodeKeyFor(type);
      const payload = { node_key: nodeKey, name: defaultNodeName(type, nodeKey), node_type: type, config_json: defaultNodeConfig(type), canvas_x: Math.round(position.x), canvas_y: Math.round(position.y), auto_connect: false };
      state.pendingNodeKeys.add(nodeKey);
      let node;
      try {
        node = await request(`${api}/scenarios/versions/${versionId}/nodes`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
      } catch (error) {
        state.pendingNodeKeys.delete(nodeKey);
        if (error.status !== 409 || attempt === 1) { notify(error.message, "danger"); return; }
        try { await loadData(); } catch (refreshError) { notify(`节点列表刷新失败：${refreshError.message}`, "danger"); return; }
        if (currentVersion()?.id !== versionId) { notify("当前版本已切换，请在新版本中重新添加节点。", "warning"); return; }
        continue;
      }
      state.pendingNodeKeys.delete(nodeKey);
      state.inspectorTab = "SETTINGS";
      try {
        await loadData();
      } catch (error) { notify(`节点已创建，但列表刷新失败：${error.message}`, "warning"); return; }
      focusWorkflowNode(node.id);
      notify(`${nodeTypeLabel(type)}节点已加入画布（${node.node_key}），可继续添加同类型节点。`);
      return;
    }
  }
  async function moveNode(nodeId, x, y) {
    if (!isDraft()) return;
    try {
      await request(`${api}/scenarios/versions/${currentVersion().id}/nodes/${nodeId}`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ canvas_x: Math.round(x), canvas_y: Math.round(y) }) });
      const node = nodeById(nodeId); if (node) { node.canvas_x = x; node.canvas_y = y; }
      renderWorkflowCanvas(); renderInspector();
    } catch (error) { notify(error.message, "danger"); }
  }
  async function connectNodes(sourceNodeKey, targetNodeKey) {
    if (!isDraft()) return;
    if (sourceNodeKey === targetNodeKey) { notify("不能连接到同一个节点。", "warning"); return; }
    try {
      await request(`${api}/scenarios/versions/${currentVersion().id}/edges`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ source_node_key: sourceNodeKey, target_node_key: targetNodeKey, mapping_json: {} }) });
      await loadData(); notify("节点连线已保存。");
    } catch (error) { notify(error.message, "danger"); }
  }
  async function deleteEdge(edgeId) {
    if (!isDraft() || !(currentVersion()?.edges || []).some((edge) => edge.id === edgeId)) return;
    byId("workflowDeleteEdge").disabled = true;
    try {
      await request(`${api}/scenarios/versions/${currentVersion().id}/edges/${edgeId}`, { method: "DELETE" });
      state.selectedEdgeId = null;
      await loadData();
      notify("连线已删除。");
    } catch (error) {
      renderWorkflowCanvas();
      notify(error.message, "danger");
    }
  }
  async function saveNodeConfig() {
    const node = nodeById(state.selectedNodeId); if (!node || !isDraft()) return;
    closeSubflowCanvas();
    let config = deepCopy(node.config_json);
    try {
      const name = byId("nodeName")?.value.trim();
      if (!name) throw new Error("节点名称不能为空。");
      if (node.node_type === "START") {
        config.inputs = [
          { name: "image_path", label: "检测图片", type: "IMAGE", required: true },
          { name: "original_image_path", label: "原始整图", type: "IMAGE", required: false },
          { name: "source_image_path", label: "原图来源路径", type: "TEXT", required: false },
          { name: "roi_bbox", label: "ROI 原图坐标", type: "BBOX", required: false },
          ...readSchemaParameterRows("nodeStartInputParameters", "流程输入参数"),
        ];
      } else if (node.node_type === "VLM") {
        const mode = byId("nodeVlmMode").value;
        config = readVlmInspectorConfig(node, { mode, validate: true, removeInactive: true });
      } else if (node.node_type === "VISION_MODEL") {
        const versionId = Number(byId("nodeVisionModelVersion").value);
        if (!versionId) throw new Error("请选择已发布训练模型版本。");
        const profile = visionOutputProfile(selectedVisionModelVersion(versionId));
        config.model_version_id = versionId;
        config.confidence = Number(byId("nodeConfidence").value);
        delete config.expected_label;
        delete config.expected_class;
        delete config.expected_count;
        delete config.expected_min_count;
        delete config.expected_max_count;
        if (profile.supports_objects) {
          config.iou = Number(byId("nodeIou").value);
        } else {
          delete config.iou;
        }
        config.input_mapping = readParameterRows("nodeInputParameters", "节点输入参数");
        config.context = config.input_mapping;
        config.output_mapping = readParameterRows("nodeOutputParameters", "节点输出参数");
      } else if (node.node_type === "IMAGE_CROP") {
        const bbox = byId("nodeCropBbox").value.trim();
        const objects = byId("nodeCropObjects").value.trim();
        if (!bbox && !objects) throw new Error("请配置单个 bbox 或目标列表 objects。");
        const compatibilityError = objects ? annotationGeometryCompatibilityError(objects, "") : cropBboxCompatibilityError(bbox);
        if (compatibilityError) throw new Error(compatibilityError);
        config.image_path = byId("nodeCropImagePath").value.trim() || "{{ input.image_path }}";
        config.objects = objects;
        config.bbox = objects ? "" : bbox;
        config.padding_ratio = Number(byId("nodeCropPadding").value);
        if (!Number.isFinite(config.padding_ratio) || config.padding_ratio < 0 || config.padding_ratio > 1) throw new Error("边缘扩展比例必须在 0 到 1 之间。");
        config.output_mapping = readParameterRows("nodeOutputParameters", "节点输出参数");
      } else if (node.node_type === "IMAGE_ANNOTATE") {
        const objects = byId("nodeAnnotateObjects").value.trim();
        const bbox = byId("nodeAnnotateBbox").value.trim();
        if (!objects && !bbox) throw new Error("请配置目标列表或单个 bbox；通常引用上游模型的 objects。");
        const compatibilityError = annotationGeometryCompatibilityError(objects, bbox);
        if (compatibilityError) throw new Error(compatibilityError);
        config.image_path = byId("nodeAnnotateImagePath").value.trim() || "{{ input.image_path }}";
        config.objects = objects;
        config.bbox = bbox;
        config.line_width = Number(byId("nodeAnnotateLineWidth").value);
        if (!Number.isFinite(config.line_width) || config.line_width < 1 || config.line_width > 20) throw new Error("画框线宽必须在 1 到 20 之间。");
        config.label_prefix = byId("nodeAnnotateLabelPrefix").value.trim();
        config.show_label = Boolean(byId("nodeAnnotateShowLabel").checked);
        config.show_confidence = Boolean(byId("nodeAnnotateShowConfidence").checked);
        config.draw_masks = Boolean(byId("nodeAnnotateDrawMasks").checked);
        config.input_mapping = readParameterRows("nodeInputParameters", "节点输入参数");
        config.context = config.input_mapping;
        config.output_mapping = readParameterRows("nodeOutputParameters", "节点输出参数");
      } else if (node.node_type === "RULE" || node.node_type === "IF") {
        config.actual = byId("nodeActual").value.trim(); config.operator = byId("nodeOperator").value;
        config.expected = config.operator === "EXISTS" ? true : byId("nodeExpected").value.trim();
        if (/\{\{\s*params\./.test(`${config.actual} ${config.expected}`)) throw new Error("旧参数映射无法直接解析，请重新选择实际值或期望值的上游变量。");
        delete config.input_mapping;
        delete config.context;
        config.output_mapping = readParameterRows("nodeOutputParameters", "节点输出参数");
      } else if (node.node_type === "WEB_API") {
        const requestFormat = byId("nodeApiRequestFormat").value;
        const bodyText = byId("nodeApiBody").value.trim();
        config.method = byId("nodeApiMethod").value; config.url = byId("nodeApiUrl").value.trim(); config.request_format = requestFormat;
        if (!config.url) throw new Error("请填写接口地址。");
        config.headers = parseJson(byId("nodeApiHeaders").value, "请求头", {});
        config.body = requestFormat === "JSON" ? parseJson(bodyText, "请求体", {}) : bodyText;
        config.response_format = byId("nodeApiResponseFormat").value;
        config.input_mapping = readParameterRows("nodeInputParameters", "节点输入参数");
        config.context = config.input_mapping;
        config.output_mapping = readParameterRows("nodeOutputParameters", "节点输出参数");
      } else if (node.node_type === "S3_UPLOAD") {
        config.image_path = byId("nodeS3ImagePath").value.trim();
        if (!config.image_path) throw new Error("请选择待上传的图片来源。");
        config.key = byId("nodeS3Key").value.trim();
        config.output_mapping = readParameterRows("nodeOutputParameters", "节点输出参数");
      } else if (node.node_type === "ITERATION" || node.node_type === "CONDITION_LOOP") {
        config.max_iterations = Number(byId("nodeLoopMaxIterations").value);
        const limit = node.node_type === "CONDITION_LOOP" ? 20 : 100;
        if (!Number.isInteger(config.max_iterations) || config.max_iterations < 1 || config.max_iterations > limit) throw new Error(`最大次数必须在 1 到 ${limit} 之间。`);
        config.body_nodes = readLoopBody();
        config.body_output = readParameterRows("nodeLoopBodyOutputParameters", "子流程输出");
        if (node.node_type === "ITERATION") {
          config.items = byId("nodeLoopItems").value.trim();
          config.item_name = byId("nodeLoopItemName").value.trim() || "item";
          config.aggregate = byId("nodeLoopBodyAggregate").value;
          if (!config.items) throw new Error("请选择待遍历的数组，例如图片裁剪节点的 crops。");
          config.input_mapping = readParameterRows("nodeInputParameters", "节点输入参数");
          config.context = config.input_mapping;
        } else {
          config.initial_state = readInitialState();
          config.state_updates = readConditionUpdates();
          config.timeout_seconds = Number(byId("nodeLoopTimeout").value);
          if (!Number.isFinite(config.timeout_seconds) || config.timeout_seconds <= 0 || config.timeout_seconds > 300) throw new Error("超时时间必须在 0 到 300 秒之间。");
          const expected = byId("nodeLoopStopExpected").value.trim();
          let parsedExpected = expected;
          if (!expected.includes("{{")) {
            try { parsedExpected = JSON.parse(expected); } catch { parsedExpected = expected; }
          }
          config.stop_condition = {
            actual: byId("nodeLoopStopActual").value.trim(),
            operator: byId("nodeLoopStopOperator").value,
            expected: parsedExpected,
          };
          if (!config.stop_condition.actual) throw new Error("请设置条件循环的退出条件。");
        }
        config.output_mapping = readParameterRows("nodeOutputParameters", "节点输出参数");
      } else if (node.node_type === "LOOP") {
        config.operation = byId("nodeLoopOperation").value;
        config.items = byId("nodeLoopItems").value.trim();
        config.item_name = byId("nodeLoopItemName").value.trim() || "item";
        config.max_iterations = Number(byId("nodeLoopMaxIterations").value);
        if (!config.items) throw new Error("请选择循环集合，例如批量裁剪节点的 crops。");
        if (!Number.isInteger(config.max_iterations) || config.max_iterations < 1 || config.max_iterations > (config.operation === "VLM" ? 30 : config.operation === "SUBFLOW" ? 100 : 1000)) throw new Error("最大次数超出允许范围。");
        if (config.operation === "SUBFLOW") {
          config.body_nodes = readLoopBody();
          config.aggregate = byId("nodeLoopBodyAggregate").value;
          config.body_output = readParameterRows("nodeLoopBodyOutputParameters", "循环体每项输出");
        }
        if (config.operation === "VLM") {
          config.vlm_model_id = Number(byId("nodeLoopVlmModel").value) || null;
          config.prompt = String(syncPromptTemplateEditor(byId("nodeLoopPromptEditor")) || "").trim();
          config.max_parallelism = Number(byId("nodeLoopParallelism").value);
          config.aggregate = byId("nodeLoopAggregate").value;
          config.vlm_parameters = { temperature: Number(byId("nodeLoopTemperature").value), max_tokens: Number(byId("nodeLoopMaxTokens").value) };
          if (!config.vlm_model_id || !config.prompt) throw new Error("逐项 VLM 检测需要选择模型并填写提示词。");
          if (!Number.isInteger(config.max_parallelism) || config.max_parallelism < 1 || config.max_parallelism > 3) throw new Error("并发数必须在 1 到 3 之间。");
        }
        config.input_mapping = readParameterRows("nodeInputParameters", "节点输入参数");
        config.context = config.input_mapping;
        config.output_mapping = readParameterRows("nodeOutputParameters", "节点输出参数");
      } else if (node.node_type === "END") {
        config.output = readParameterRows("nodeEndOutputParameters", "对外返回参数");
      }
      await request(`${api}/scenarios/versions/${currentVersion().id}/nodes/${node.id}`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name, config_json: config }) });
      if (node.node_type === "IF") {
        const branchSelects = [...document.querySelectorAll("[data-edge-branch]")];
        await Promise.all(branchSelects.map((select) => request(`${api}/scenarios/versions/${currentVersion().id}/edges/${select.dataset.edgeBranch}`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ mapping_json: select.value ? { branch: select.value } : {} }) })));
      }
      state.loopBodyNodeId = null;
      await loadData(); notify("节点配置已保存。");
    } catch (error) { notify(error.message, "danger"); }
  }
  async function deleteSelectedNode() {
    const node = nodeById(state.selectedNodeId); if (!node || !isDraft()) return;
    if (!confirm(`删除节点“${node.name}”及其连线吗？`)) return;
    try { await request(`${api}/scenarios/versions/${currentVersion().id}/nodes/${node.id}`, { method: "DELETE" }); state.selectedNodeId = null; await loadData(); notify("节点已删除。"); } catch (error) { notify(error.message, "danger"); }
  }

  function setZoom(value) {
    state.zoom = Math.max(0.5, Math.min(1.5, Number(value.toFixed(2))));
    renderWorkflowCanvas();
  }
  function beginNodeDrag(event, element) {
    if (!isDraft() || event.button !== 0 || state.subflowNodeId !== null) return;
    const nodeId = Number(element.dataset.nodeId); const node = nodeById(nodeId); if (!node) return;
    state.drag = { nodeId, startX: event.clientX, startY: event.clientY, original: nodePosition(node) };
    element.setPointerCapture?.(event.pointerId); event.preventDefault();
  }
  function moveNodeDrag(event) {
    if (!state.drag) return;
    const x = Math.max(20, Math.min(canvasWidth - nodeWidth - 20, state.drag.original.x + (event.clientX - state.drag.startX) / state.zoom));
    const y = Math.max(20, Math.min(canvasHeight - nodeHeight - 20, state.drag.original.y + (event.clientY - state.drag.startY) / state.zoom));
    const node = nodeById(state.drag.nodeId); if (!node) return;
    node.canvas_x = x; node.canvas_y = y; renderWorkflowCanvas();
  }
  async function endNodeDrag() {
    if (!state.drag) return;
    const drag = state.drag; state.drag = null;
    const node = nodeById(drag.nodeId); if (node) await moveNode(node.id, node.canvas_x, node.canvas_y);
  }
  function beginConnection(event, sourceNodeKey) {
    if (!isDraft()) return;
    state.connecting = sourceNodeKey;
    byId("workflowCanvasSurface").classList.add("connecting");
    event.preventDefault(); event.stopPropagation();
  }
  async function endConnection(event, targetNodeKey) {
    if (!state.connecting) return;
    const source = state.connecting; state.connecting = null;
    byId("workflowCanvasSurface").classList.remove("connecting");
    event.preventDefault(); event.stopPropagation();
    await connectNodes(source, targetNodeKey);
  }

  document.addEventListener("DOMContentLoaded", () => {
    byId("designerVersionSelect").addEventListener("change", (event) => selectSceneVersion(event.target.value));
    byId("designerVersionHistory").addEventListener("click", openVersionHistory);
    byId("sceneVersionList").addEventListener("click", (event) => {
      const button = event.target.closest("[data-select-scene-version]");
      if (button) selectSceneVersion(button.dataset.selectSceneVersion, { closeHistory: true });
    });
    byId("designerSave").addEventListener("click", saveDesigner);
    byId("directSave").addEventListener("click", () => saveDirect());
    byId("designerClone").addEventListener("click", cloneVersion);
    byId("designerValidate").addEventListener("click", () => runDesignerPreflight());
    byId("designerPublish").addEventListener("click", publishVersion);
    byId("designerApi").addEventListener("click", showSceneApiContract);
    byId("copySceneApiEndpoint").addEventListener("click", () => copySceneApiValue("endpoint"));
    byId("copySceneApiRequest").addEventListener("click", () => copySceneApiValue("request"));
    byId("copySceneApiResponse").addEventListener("click", () => copySceneApiValue("response"));
    byId("directTestImage").addEventListener("change", (event) => previewSelectedImage(event.target.files?.[0]));
    byId("directTestInputFields").addEventListener("input", rememberDirectTestValues);
    byId("directPromptVariables").addEventListener("click", (event) => {
      const button = event.target.closest("[data-direct-prompt-variable]");
      if (button) insertDirectPromptVariable(button.dataset.directPromptVariable);
    });
    byId("directRunTest").addEventListener("click", runDirectTest);
    byId("directClearResult").addEventListener("click", clearDirectConversation);
    window.addEventListener("resize", fitDirectDesignerToViewport);
    window.addEventListener("resize", () => { if (state.subflowNodeId !== null) renderWorkflowCanvas(); });
    byId("workflowZoomIn").addEventListener("click", () => setZoom(state.zoom + 0.1));
    byId("workflowZoomOut").addEventListener("click", () => setZoom(state.zoom - 0.1));
    byId("workflowZoomReset").addEventListener("click", () => setZoom(1));
    byId("workflowFitCanvas").addEventListener("click", fitWorkflowCanvas);
    byId("workflowNodeNavigator").addEventListener("change", (event) => {
      if (event.target.value) focusWorkflowNode(Number(event.target.value));
    });
    byId("workflowAutoLayout").addEventListener("click", autoLayoutWorkflow);
    byId("workflowDeleteEdge").addEventListener("click", () => {
      if (state.selectedEdgeId !== null) deleteEdge(state.selectedEdgeId);
    });
    byId("workflowTestImage").addEventListener("change", (event) => previewWorkflowTestImage(event.target.files?.[0]));
    byId("workflowRunTest").addEventListener("click", runWorkflowTest);
    byId("workflowTestRunPanel").addEventListener("click", runWorkflowTest);
    byId("workflowTestClear").addEventListener("click", clearWorkflowTest);
    byId("workflowNodeSearch").addEventListener("input", (event) => filterWorkflowPalette(event.target.value));
    byId("workflowPaletteToggle").addEventListener("click", () => setWorkbenchPanel("palette", !state.paletteCollapsed));
    byId("workflowPaletteClose").addEventListener("click", () => setWorkbenchPanel("palette", true));
    byId("workflowInspectorToggle").addEventListener("click", () => setWorkbenchPanel("inspector", !state.inspectorCollapsed));
    byId("workflowInspectorClose").addEventListener("click", () => setWorkbenchPanel("inspector", true));
    document.querySelectorAll(".palette-node").forEach((node) => node.addEventListener("dragstart", (event) => { event.dataTransfer.setData("application/vision-node", node.dataset.nodeType); event.dataTransfer.effectAllowed = "copy"; }));
    byId("workflowCanvasViewport").addEventListener("dragover", (event) => event.preventDefault());
    byId("workflowCanvasViewport").addEventListener("drop", (event) => { event.preventDefault(); if (event.target.closest("#workflowSubflowPanel")) return; const type = event.dataTransfer.getData("application/vision-node"); if (type) createWorkflowNode(type, event); });
    byId("workflowSubflowPanel").addEventListener("dragover", (event) => {
      if (!isDraft()) return;
      event.preventDefault();
      event.dataTransfer.dropEffect = "copy";
      event.currentTarget.classList.add("drop-target");
    });
    byId("workflowSubflowPanel").addEventListener("dragleave", (event) => {
      if (!event.currentTarget.contains(event.relatedTarget)) event.currentTarget.classList.remove("drop-target");
    });
    byId("workflowSubflowPanel").addEventListener("drop", (event) => {
      event.currentTarget.classList.remove("drop-target");
      dropNodeIntoLoop(event, state.subflowNodeId);
    });
    byId("workflowSubflowClose").addEventListener("click", closeSubflowCanvas);
    byId("workflowSubflowSave").addEventListener("click", () => { closeSubflowCanvas(); saveNodeConfig(); });
    byId("workflowSubflowPanel").addEventListener("click", handleLoopBodyAction);
    byId("workflowNodeLayer").addEventListener("dragover", (event) => {
      const element = event.target.closest("[data-node-id]");
      if (!isDraft() || !acceptsLoopDrop(nodeById(Number(element?.dataset.nodeId)))) return;
      event.preventDefault();
      event.dataTransfer.dropEffect = "copy";
      element.classList.add("drop-target");
    });
    byId("workflowNodeLayer").addEventListener("dragleave", (event) => {
      const element = event.target.closest("[data-node-id]");
      if (element && !element.contains(event.relatedTarget)) element.classList.remove("drop-target");
    });
    byId("workflowNodeLayer").addEventListener("drop", (event) => {
      const element = event.target.closest("[data-node-id]");
      if (!acceptsLoopDrop(nodeById(Number(element?.dataset.nodeId)))) return;
      element.classList.remove("drop-target");
      dropNodeIntoLoop(event, Number(element.dataset.nodeId));
    });
    byId("workflowNodeLayer").addEventListener("pointerdown", (event) => { if (event.target.closest("[data-subflow-open]")) return; const port = event.target.closest("[data-output-node]"); if (port) { beginConnection(event, port.dataset.outputNode); return; } const element = event.target.closest("[data-node-id]"); if (element) beginNodeDrag(event, element); });
    byId("workflowNodeLayer").addEventListener("pointerup", (event) => { const port = event.target.closest("[data-input-node]"); if (port) { endConnection(event, port.dataset.inputNode); return; } endNodeDrag(); });
    window.addEventListener("pointermove", moveNodeDrag);
    window.addEventListener("pointerup", () => { if (state.connecting) { state.connecting = null; byId("workflowCanvasSurface").classList.remove("connecting"); } endNodeDrag(); });
    byId("workflowNodeLayer").addEventListener("click", (event) => { const subflow = event.target.closest("[data-subflow-open]"); if (subflow) { openSubflowCanvas(Number(subflow.dataset.subflowOpen)); return; } const element = event.target.closest("[data-node-id]"); if (!element || event.target.closest(".workflow-port")) return; closeSubflowCanvas(); state.selectedNodeId = Number(element.dataset.nodeId); state.selectedEdgeId = null; openInspectorForNode(); renderWorkflowCanvas(); renderInspector(); });
    byId("workflowEdgeLayer").addEventListener("click", (event) => {
      const edge = event.target.closest("[data-edge-id]");
      if (!edge || !isDraft()) return;
      closeSubflowCanvas();
      state.selectedEdgeId = Number(edge.dataset.edgeId);
      state.selectedNodeId = null;
      setWorkbenchPanel("inspector", true);
      renderWorkflowCanvas();
      renderInspector();
    });
    document.addEventListener("keydown", (event) => {
      if (!isDraft() || state.selectedEdgeId === null || !["Delete", "Backspace"].includes(event.key)) return;
      if (event.target.closest("input, textarea, select, [contenteditable], [role='textbox']")) return;
      event.preventDefault();
      deleteEdge(state.selectedEdgeId);
    });
    document.addEventListener("click", (event) => {
      const add = event.target.closest("[data-parameter-add]");
      const remove = event.target.closest("[data-parameter-remove]");
      if (add) {
        if (isDraft() && !add.disabled) addParameterRow(add.dataset.parameterAdd);
        return;
      }
      if (remove) removeParameterRow(remove);
    });
    const workflowInspector = byId("workflowInspector");
    workflowInspector.addEventListener("focusin", (event) => {
      const target = event.target.closest?.("[data-variable-target]");
      if (isEditableVariableTarget(target)) {
        state.variableTarget = target;
        if (isPromptTemplateEditor(target)) capturePromptSelection(target);
      }
    });
    workflowInspector.addEventListener("input", (event) => {
      if (event.target.id === "nodeActual") updateRuleActualSourceHint();
      const editor = event.target.closest?.("[data-prompt-template-editor]");
      if (!isPromptTemplateEditor(editor)) return;
      syncPromptTemplateEditor(editor);
      capturePromptSelection(editor);
      state.variableTarget = editor;
    });
    workflowInspector.addEventListener("keyup", (event) => {
      const editor = event.target.closest?.("[data-prompt-template-editor]");
      if (!isPromptTemplateEditor(editor)) return;
      capturePromptSelection(editor);
      state.variableTarget = editor;
    });
    workflowInspector.addEventListener("pointerup", (event) => {
      const editor = event.target.closest?.("[data-prompt-template-editor]");
      if (!isPromptTemplateEditor(editor)) return;
      capturePromptSelection(editor);
      state.variableTarget = editor;
    });
    workflowInspector.addEventListener("keydown", (event) => {
      const target = event.target.closest?.("[data-variable-target]");
      if (!isEditableVariableTarget(target)) return;
      const currentValue = isPromptTemplateEditor(target) ? serializePromptTemplate(target) : String(target.value || "");
      const shouldOpen = event.key === "{" || (event.key === "/" && !currentValue.trim());
      if (!shouldOpen) return;
      event.preventDefault();
      openVariablePicker(target);
    });
    workflowInspector.addEventListener("click", (event) => {
      if (event.target.closest("#nodeLoopUpdateAdd")) {
        byId("nodeLoopUpdates")?.insertAdjacentHTML("beforeend", conditionUpdateRowMarkup());
        byId("nodeLoopUpdates")?.lastElementChild?.querySelector("[data-update-key]")?.focus();
        return;
      }
      const updateRemove = event.target.closest("[data-update-remove]");
      if (updateRemove) { updateRemove.closest("[data-condition-update-row]")?.remove(); return; }
      if (handleLoopBodyAction(event)) return;
      const promptVariableRemove = event.target.closest?.("[data-prompt-variable-remove]");
      const save = event.target.closest("#saveNodeConfig");
      const remove = event.target.closest("#deleteNode");
      const tab = event.target.closest("[data-inspector-tab]");
      const picker = event.target.closest("[data-variable-picker]");
      if (promptVariableRemove) {
        const chip = promptVariableRemove.closest("[data-prompt-variable-token]");
        const editor = chip?.closest("[data-prompt-template-editor]");
        if (chip && editor && isDraft()) {
          const marker = document.createTextNode("\u200B");
          chip.replaceWith(marker);
          const range = document.createRange();
          range.setStartAfter(marker);
          range.collapse(true);
          const selection = window.getSelection();
          selection?.removeAllRanges();
          selection?.addRange(range);
          editor.focus();
          syncPromptTemplateEditor(editor);
          capturePromptSelection(editor);
          state.variableTarget = editor;
        }
        return;
      }
      if (tab) {
        setInspectorTab(tab.dataset.inspectorTab);
        return;
      }
      if (picker) {
        const target = picker.closest(".variable-control")?.querySelector("[data-variable-target]");
        openVariablePicker(target);
        return;
      }
      if (save) saveNodeConfig();
      if (remove) deleteSelectedNode();
    });
    document.addEventListener("input", (event) => {
      if (event.target.id !== "workflowVariablePickerSearch") return;
      state.variablePickerQuery = event.target.value;
      renderVariablePicker();
    });
    document.addEventListener("click", (event) => {
      const close = event.target.closest?.("[data-variable-picker-close]");
      const option = event.target.closest?.("[data-variable-picker-token]");
      if (close) {
        closeVariablePicker();
        return;
      }
      if (option) applyVariablePickerToken(option.dataset.variablePickerToken);
    });
    document.addEventListener("pointerdown", (event) => {
      const picker = byId("workflowVariablePicker");
      if (!picker || picker.hidden) return;
      if (picker.contains(event.target) || event.target.closest?.("[data-variable-picker]") || event.target.closest?.("[data-variable-target]")) return;
      closeVariablePicker();
    });
    window.addEventListener("resize", () => {
      if (!byId("workflowVariablePicker")?.hidden) renderVariablePicker();
    });
    byId("workflowInspector").addEventListener("change", (event) => {
      if (event.target.id === "nodeLoopOperation") {
        const loopVlm = event.target.value === "VLM";
        byId("nodeLoopVlmFields").hidden = !loopVlm;
        byId("nodeLoopSubflowFields").hidden = event.target.value !== "SUBFLOW";
        byId("nodeLoopMaxIterations").max = loopVlm ? "30" : event.target.value === "SUBFLOW" ? "100" : "1000";
        byId("nodeLoopItems").dataset.variableAcceptTypes = loopVlm ? "IMAGE_LIST|JSON" : "JSON|OBJECT_LIST|IMAGE_LIST";
        return;
      }
      if (event.target.id === "nodeOperator") {
        const expectedField = byId("nodeExpectedField");
        if (expectedField) expectedField.hidden = event.target.value === "EXISTS";
        return;
      }
      if (event.target.id === "nodeVlmMode") {
        const node = nodeById(state.selectedNodeId);
        if (!node) return;
        const currentMode = workflowVlmMode(node.config_json);
        node.config_json = readVlmInspectorConfig(node, { mode: currentMode });
        node.config_json.vlm_mode = event.target.value === "SCENE" ? "SCENE" : "CUSTOM";
        renderInspector();
        return;
      }
      if (event.target.id === "nodeReferencedScene") {
        const node = nodeById(state.selectedNodeId);
        if (!node) return;
        node.config_json = readVlmInspectorConfig(node, { mode: "SCENE" });
        renderInspector();
        return;
      }
      if (event.target.id === "nodeVisionModelVersion") {
        const node = nodeById(state.selectedNodeId);
        if (!node) return;
        node.config_json = {
          ...(node.config_json || {}),
          model_version_id: Number(event.target.value) || null,
        };
        renderInspector();
      }
    });
    loadData({ preserveVersion: false }).catch((error) => notify(error.message, "danger"));
  });
})();
