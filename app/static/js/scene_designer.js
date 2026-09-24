(() => {
  const api = "/api/v1";
  const canvasWidth = 2200;
  const canvasHeight = 1320;
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
      throw new Error(detail);
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
    START: "开始", END: "结束", VLM: "VLM 检测", VISION_MODEL: "训练模型", IMAGE_CROP: "图片裁剪", RULE: "规则判断", WEB_API: "Web 接口", IF: "条件分支", LOOP: "循环控制",
  }[String(type || "").toUpperCase()] || type);
  const nodeTypeIcon = (type) => ({
    START: "▶", END: "■", VLM: "◇", VISION_MODEL: "◉", IMAGE_CROP: "✂", RULE: "✓", WEB_API: "↗", IF: "?", LOOP: "↻",
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
      .filter((field) => !["image", "image_path", "image_paths", "image_file"].includes(String(field.name || "").toLowerCase()))
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
      if (["image", "image_path", "image_paths", "image_file"].includes(name.toLowerCase())) {
        throw new Error(`“${name}”由系统自动传入，不能重复声明。`);
      }
      if (seen.has(name)) throw new Error(`场景字段“${name}”重复。`);
      seen.add(name);
      fields.push({ name, label, type: "TEXT", required: false });
    });
    return fields;
  }

  const imageInputNames = new Set(["image", "image_path", "image_paths", "image_file"]);
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
    dataAttributes = "",
  } = {}) {
    const fieldId = id ? ` id="${escapeHtml(id)}"` : "";
    const field = rows
      ? `<textarea${fieldId} class="${escapeHtml(className)}" data-variable-target data-variable-scope="${escapeHtml(scope)}" data-variable-mode="${escapeHtml(mode)}" ${dataAttributes} aria-label="${escapeHtml(ariaLabel)}" placeholder="${escapeHtml(placeholder)}" rows="${Number(rows)}" ${readonly ? "readonly" : ""}>${escapeHtml(value)}</textarea>`
      : `<input${fieldId} class="${escapeHtml(className)}" data-variable-target data-variable-scope="${escapeHtml(scope)}" data-variable-mode="${escapeHtml(mode)}" ${dataAttributes} aria-label="${escapeHtml(ariaLabel)}" value="${escapeHtml(value)}" placeholder="${escapeHtml(placeholder)}" type="${escapeHtml(type)}" ${readonly ? "readonly" : ""}>`;
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
    const apiButton = byId("designerApi");
    const hasPublishedVersion = Boolean(state.scene.published_version_id);
    apiButton.disabled = !hasPublishedVersion;
    apiButton.title = hasPublishedVersion
      ? "查看当前已发布版本的外部调用接口"
      : "请先发布场景后再查看外部调用接口";
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
    byId("directDraftTip").textContent = isDraft() ? "草稿可随时测试" : "已发布版本只读；请先创建草稿版本";
    requestAnimationFrame(fitDirectDesignerToViewport);
  }

  function nodeById(id) { return currentVersion()?.nodes.find((node) => node.id === Number(id)); }
  function nodeByKey(key) { return currentVersion()?.nodes.find((node) => node.node_key === key); }
  function defaultNodeName(type) {
    const count = (currentVersion()?.nodes || []).filter((node) => node.node_type === type).length + 1;
    const label = nodeTypeLabel(type);
    return `${label}${count}`;
  }
  function defaultNodeConfig(type) {
    if (type === "VLM") return { vlm_mode: "CUSTOM", prompt: "请检查图片并严格按 JSON 返回 result、confidence、reason。", context: {}, vlm_parameters: { temperature: 0, max_tokens: 1024 } };
    if (type === "VISION_MODEL") return { confidence: 0.25, iou: 0.45, expected_min_count: 1, context: {} };
    if (type === "IMAGE_CROP") return { image_path: "{{ input.image_path }}", bbox: "", padding_ratio: 0.05, output_mapping: {} };
    if (type === "RULE") return { actual: "{{ nodes.previous.result }}", operator: "EQUALS", expected: "OK" };
    if (type === "WEB_API") return { method: "POST", url: "", headers: {}, request_format: "JSON", body: {}, response_format: "JSON", context: {} };
    if (type === "IF") return { actual: "{{ nodes.previous.result }}", operator: "EQUALS", expected: "OK" };
    if (type === "LOOP") return { items: "{{ input.items }}", item_name: "item", max_iterations: 10 };
    return {};
  }
  function nodeKeyFor(type) {
    const prefix = ({ VLM: "vlm", VISION_MODEL: "model", IMAGE_CROP: "crop", RULE: "rule", WEB_API: "api", IF: "if", LOOP: "loop" }[type] || "node");
    let index = 1;
    const keys = new Set((currentVersion()?.nodes || []).map((node) => node.node_key));
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
  function edgePath(source, target) {
    const start = nodePosition(source); const end = nodePosition(target);
    const x1 = start.x + nodeWidth; const y1 = start.y + nodeHeight / 2;
    const x2 = end.x; const y2 = end.y + nodeHeight / 2;
    const offset = Math.max(90, Math.abs(x2 - x1) * 0.45);
    return `M ${x1} ${y1} C ${x1 + offset} ${y1}, ${x2 - offset} ${y2}, ${x2} ${y2}`;
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
    surface.style.width = `${canvasWidth}px`;
    surface.style.height = `${canvasHeight}px`;
    surface.style.transform = `scale(${state.zoom})`;
    surface.style.transformOrigin = "0 0";
    const nodeLayer = byId("workflowNodeLayer");
    const nodes = [...(version.nodes || [])].filter((node) => node.enabled).sort((a, b) => a.sort_order - b.sort_order || a.id - b.id);
    fitWorkflowToViewport(nodes);
    surface.style.transform = `scale(${state.zoom})`;
    nodeLayer.innerHTML = nodes.map((node) => {
      const position = nodePosition(node);
      const systemNode = ["START", "END"].includes(node.node_type);
      const selected = node.id === state.selectedNodeId;
      return `<article class="workflow-canvas-node workflow-node-${String(node.node_type).toLowerCase()} ${selected ? "selected" : ""} ${systemNode ? "system-node" : ""}" data-node-id="${node.id}" style="left:${position.x}px;top:${position.y}px">
        <button class="workflow-port workflow-input-port" data-input-node="${escapeHtml(node.node_key)}" type="button" title="输入端点"></button>
        <div class="workflow-node-content"><div class="workflow-node-label"><span class="workflow-node-icon" aria-hidden="true">${nodeTypeIcon(node.node_type)}</span><small>${escapeHtml(nodeTypeLabel(node.node_type))}</small></div><strong>${escapeHtml(node.name)}</strong><span class="workflow-node-key">${escapeHtml(node.node_key)}</span></div>
        <button class="workflow-port workflow-output-port" data-output-node="${escapeHtml(node.node_key)}" type="button" title="输出端点"></button>
      </article>`;
    }).join("");
    const edges = (version.edges || []).filter((edge) => nodeByKey(edge.source_node_key) && nodeByKey(edge.target_node_key));
    const edgeLayer = byId("workflowEdgeLayer");
    edgeLayer.setAttribute("viewBox", `0 0 ${canvasWidth} ${canvasHeight}`);
    edgeLayer.setAttribute("width", String(canvasWidth));
    edgeLayer.setAttribute("height", String(canvasHeight));
    edgeLayer.innerHTML = `<defs><marker id="workflowArrow" markerWidth="10" markerHeight="10" refX="8" refY="3" orient="auto" markerUnits="strokeWidth"><path d="M0,0 L0,6 L9,3 z"></path></marker></defs>${edges.map((edge) => {
      const source = nodeByKey(edge.source_node_key); const target = nodeByKey(edge.target_node_key);
      const branch = edge.mapping_json?.branch;
      return `<path class="workflow-edge" data-edge-id="${edge.id}" d="${edgePath(source, target)}" marker-end="url(#workflowArrow)"></path>${branch ? `<text class="workflow-edge-label" x="${(nodePosition(source).x + nodePosition(target).x + nodeWidth) / 2}" y="${(nodePosition(source).y + nodePosition(target).y + nodeHeight) / 2 - 9}">${escapeHtml(branch)}</text>` : ""}`;
    }).join("")}`;
    byId("workflowCanvasEmpty").hidden = nodes.length > 2;
    const workflowHint = byId("workflowCanvasHint");
    if (workflowHint) workflowHint.textContent = isDraft() ? "拖拽节点、拖动端点连线；发布前会校验有效的“开始 → 结束”路径。" : "已发布版本只读；请新建草稿版本后修改画布。";
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
      output_keys: ["result", "confidence", "reason", "task_type", "detection_count", "objects", "objects.0.class_id", "objects.0.label", "objects.0.confidence", "objects.0.bbox"],
    },
    YOLO_SEGMENTATION: {
      task_type: "YOLO_SEGMENTATION", display_name: "YOLO 目标分割", summary: "当前 YOLO 分割适配器输出目标列表、bbox 和 mask。",
      supports_objects: true, supports_bbox: true, supports_mask: true, supports_classification: false,
      output_keys: ["result", "confidence", "reason", "task_type", "detection_count", "objects", "objects.0.class_id", "objects.0.label", "objects.0.confidence", "objects.0.bbox", "objects.0.mask"],
    },
    YOLO_CLASSIFICATION: {
      task_type: "YOLO_CLASSIFICATION", display_name: "YOLO 图像分类", summary: "输出 Top1 / Top5 类别和置信度，不输出 bbox。",
      supports_objects: false, supports_bbox: false, supports_mask: false, supports_classification: true,
      output_keys: ["result", "confidence", "reason", "task_type", "classification", "classification.top1_class_id", "classification.top1_label", "classification.top1_confidence", "classification.top5"],
    },
  });

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

  function nodeOutputKeys(node) {
    const defaults = {
      VLM: ["result", "confidence", "reason"],
      VISION_MODEL: visionOutputKeysForNode(node),
      IMAGE_CROP: ["result", "image_path", "source_image_path", "requested_bbox", "crop_bbox", "width", "height", "padding_ratio"],
      RULE: ["result", "actual", "expected", "operator"],
      IF: ["result", "branch", "actual", "expected", "operator"],
      WEB_API: ["result", "confidence", "status_code", "response"],
      LOOP: ["result", "items", "count"],
      END: ["result", "confidence", "reason"],
    };
    const nodeType = String(node?.node_type || "").toUpperCase();
    const aliases = Object.keys((node?.config_json || {}).output_mapping || {});
    return [...new Set([...(defaults[nodeType] || ["result"]), ...aliases])];
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
    const keys = [...new Set(["result", "confidence", "reason", ...nodeOutputKeys(node)])];
    return [
      ...keys.map((key) => ({ label: key, token: `{{ response.${key} }}` })),
      { label: "完整原始输出", token: "{{ response }}" },
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
      { label: "检测图片", token: "{{ input.image_path }}" },
      ...fields
        .filter((field) => !imageInputNames.has(String(field.name || "").toLowerCase()))
        .map((field) => ({ label: field.label || field.name, token: `{{ input.${field.name} }}` })),
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
            items: keys.map((key) => ({ label: key, token: `{{ params.${key} }}` })),
          }]
          : [];
      })()
      : [];
    return [
      ...promptGroups,
      { label: "开始 / 系统输入", description: "接口入参和开始节点字段", items: inputItems },
      ...nodes.map((item) => ({
        label: `${item.name} · ${item.node_key}`,
        description: nodeTypeLabel(item.node_type),
        items: [
          ...nodeOutputKeys(item).map((key) => ({ label: key, token: `{{ nodes.${item.node_key}.${key} }}` })),
          { label: "完整输出", token: `{{ nodes.${item.node_key} }}` },
        ],
      })),
    ];
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
    const groups = variableGroups(node, scope)
      .map((group) => ({ ...group, items: group.items.filter((item) => variableMatchesQuery(item, group, query)) }))
      .filter((group) => group.items.length);
    const list = groups.length
      ? groups.map((group) => `<section class="workflow-variable-group"><header><strong>${escapeHtml(group.label)}</strong>${group.description ? `<small>${escapeHtml(group.description)}</small>` : ""}</header>${group.items.map((item) => `<button type="button" class="workflow-variable-option" data-variable-picker-token="${escapeHtml(item.token)}"><span>${escapeHtml(item.label)}</span><code>${escapeHtml(item.token)}</code></button>`).join("")}</section>`).join("")
      : '<div class="workflow-variable-empty">没有匹配的可用变量</div>';
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
    const inputContent = target.querySelector("#workflowNodeInputContent");
    const outputContent = target.querySelector("#workflowNodeOutputContent");
    const parameterSection = (element) => element?.closest(".node-parameter-section") || element;
    const inputSections = [...new Set([...target.querySelectorAll("#nodeInputParameters, #nodeStartInputParameters")]
      .map(parameterSection)
      .filter(Boolean))];
    const outputSections = [...new Set([...target.querySelectorAll("#nodeOutputParameters, #nodeEndOutputParameters")]
      .map(parameterSection)
      .filter(Boolean))];
    inputSections.forEach((section) => inputContent?.append(section));
    outputSections.forEach((section) => outputContent?.append(section));
    if (inputPanel) inputPanel.hidden = inputSections.length === 0;
    if (outputPanel) outputPanel.hidden = outputSections.length === 0;

    const tabList = target.querySelector(".node-config-tabs");
    const tabs = [
      { key: "SETTINGS", label: "设置", panel: settingsPanel },
      { key: "INPUT", label: "输入", panel: inputPanel },
      { key: "OUTPUT", label: "输出", panel: outputPanel },
    ].filter((item) => item.panel && !item.panel.hidden);
    if (tabList) {
      tabList.innerHTML = tabs.map((item) => `<button type="button" class="node-config-tab" data-inspector-tab="${item.key}" role="tab" aria-selected="false">${item.label}</button>`).join("");
    }
    if (!tabs.some((item) => item.key === state.inspectorTab)) state.inspectorTab = "SETTINGS";
    setInspectorTab(state.inspectorTab);
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
        ? `<div class="node-model-task-note"><strong>${escapeHtml(visionTaskLabel(selectedModel))}</strong>${visionCapabilityMarkup(profile)}<p>分类模型输出 <code>classification.top1_label</code>。如需对检测框内的局部图片分类，请先接入“图片裁剪”节点，再在下方输入参数中将 <code>image_path</code> 映射为裁剪节点输出。</p></div><div class="form-row"><label><span>期望类别</span><input id="nodeExpectedLabel" class="form-control" value="${escapeHtml(config.expected_label || config.expected_class || "")}" placeholder="例如 locked" ${readonly ? "readonly" : ""}></label><label><span>置信度阈值</span><input id="nodeConfidence" class="form-control" type="number" min="0" max="1" step="0.01" value="${escapeHtml(config.confidence ?? 0.25)}" ${readonly ? "readonly" : ""}></label></div>`
        : profile.supports_objects
          ? `<div class="node-model-task-note"><strong>${escapeHtml(visionTaskLabel(selectedModel))}</strong>${visionCapabilityMarkup(profile)}<p>${profile.supports_bbox ? '可把 <code>objects.0.bbox</code> 传给“图片裁剪”节点，再把裁剪图片交给下游 VLM 或分类模型。' : '该模型输出目标结果但未声明 bbox；不能直接连接图片裁剪节点。'}</p></div><div class="form-row"><label><span>期望类别（可选）</span><input id="nodeExpectedClass" class="form-control" value="${escapeHtml(config.expected_class || "")}" placeholder="例如 harness" ${readonly ? "readonly" : ""}></label><label><span>最小数量</span><input id="nodeExpectedMinCount" class="form-control" type="number" min="0" value="${escapeHtml(config.expected_min_count ?? 1)}" ${readonly ? "readonly" : ""}></label></div><div class="form-row"><label><span>最大数量（可选）</span><input id="nodeExpectedMaxCount" class="form-control" type="number" min="0" value="${escapeHtml(config.expected_max_count ?? "")}" ${readonly ? "readonly" : ""}></label><label><span>置信度阈值</span><input id="nodeConfidence" class="form-control" type="number" min="0" max="1" step="0.01" value="${escapeHtml(config.confidence ?? 0.25)}" ${readonly ? "readonly" : ""}></label></div><label><span>IoU 阈值</span><input id="nodeIou" class="form-control" type="number" min="0" max="1" step="0.01" value="${escapeHtml(config.iou ?? 0.45)}" ${readonly ? "readonly" : ""}></label>`
          : `<div class="node-model-task-note"><strong>${escapeHtml(visionTaskLabel(selectedModel))}</strong>${visionCapabilityMarkup(profile)}<p>请先由该模型适配器声明实际输出字段后，再把特定字段映射给后续节点。</p></div><label><span>置信度阈值</span><input id="nodeConfidence" class="form-control" type="number" min="0" max="1" step="0.01" value="${escapeHtml(config.confidence ?? 0.25)}" ${readonly ? "readonly" : ""}></label>`;
      fields = `<label><span>已发布训练模型版本</span><select id="nodeVisionModelVersion" class="form-select" ${readonly ? "disabled" : ""}>${modelVersionOptions(config.model_version_id)}</select></label>${taskFields}${inputMappingMarkup(config, node, readonly)}${outputMappingMarkup(config, readonly)}`;
    } else if (node.node_type === "IMAGE_CROP") {
      fields = `<div class="node-model-task-note"><strong>将定位框转换为局部图片</strong><p>bbox 使用像素坐标 <code>[x1, y1, x2, y2]</code>。点击字段右侧 <code>{ }</code>，从可用上游节点中选择图片或 bbox；固定坐标也可以直接填写。</p>${cropBboxSourceHint(node)}</div>${variableFieldMarkup({ label: "输入图片", id: "nodeCropImagePath", value: config.image_path || "{{ input.image_path }}", placeholder: "{{ input.image_path }}", scope: "INPUT", ariaLabel: "输入图片", readonly })}${variableFieldMarkup({ label: "裁剪框（bbox）", id: "nodeCropBbox", value: config.bbox || "", placeholder: "{{ nodes.model_1.objects.0.bbox }} 或 [20, 40, 320, 280]", scope: "INPUT", ariaLabel: "裁剪框", readonly })}<label><span>边缘扩展比例</span><input id="nodeCropPadding" class="form-control" type="number" min="0" max="1" step="0.01" value="${escapeHtml(config.padding_ratio ?? 0.05)}" ${readonly ? "readonly" : ""}></label>${outputMappingMarkup(config, readonly)}`;
    } else if (node.node_type === "RULE" || node.node_type === "IF") {
      const isIf = node.node_type === "IF";
      fields = `${variableFieldMarkup({ label: isIf ? "判断值" : "实际值", id: "nodeActual", value: config.actual || "", placeholder: "{{ nodes.vlm_1.result }}", scope: "INPUT", ariaLabel: isIf ? "判断值" : "实际值", readonly })}<div class="form-row"><label><span>运算符</span><select id="nodeOperator" class="form-select" ${readonly ? "disabled" : ""}>${["EQUALS", "CONTAINS", "EXISTS", "NUMBER_EQUALS", "NUMBER_GT", "NUMBER_GTE", "NUMBER_LT", "NUMBER_LTE"].map((operator) => `<option value="${operator}" ${config.operator === operator ? "selected" : ""}>${operator}</option>`).join("")}</select></label>${variableFieldMarkup({ label: "期望值", id: "nodeExpected", value: config.expected ?? "", scope: "INPUT", ariaLabel: "期望值", readonly })}</div>${inputMappingMarkup(config, node, readonly)}${isIf ? '<p class="muted-copy">条件分支会输出 <code>branch=true/false</code>。选中该节点后，可在下方设置每条连线对应的分支。</p>' : ""}${outputMappingMarkup(config, readonly)}`;
    } else if (node.node_type === "WEB_API") {
      fields = `<div class="form-row"><label><span>请求方法</span><select id="nodeApiMethod" class="form-select" ${readonly ? "disabled" : ""}>${["GET", "POST", "PUT", "PATCH", "DELETE"].map((method) => `<option value="${method}" ${(config.method || "POST") === method ? "selected" : ""}>${method}</option>`).join("")}</select></label><label><span>请求格式</span><select id="nodeApiRequestFormat" class="form-select" ${readonly ? "disabled" : ""}><option value="JSON" ${(config.request_format || "JSON") === "JSON" ? "selected" : ""}>JSON</option><option value="TEXT" ${(config.request_format || "JSON") === "TEXT" ? "selected" : ""}>Text</option></select></label></div>${variableFieldMarkup({ label: "接口地址", id: "nodeApiUrl", value: config.url || "", placeholder: "https://service.example.com/api/check", scope: "INPUT", mode: "INSERT", ariaLabel: "接口地址", readonly })}${variableFieldMarkup({ label: "请求头（JSON）", id: "nodeApiHeaders", value: Object.keys(config.headers || {}).length ? JSON.stringify(config.headers, null, 2) : "", className: "form-control node-textarea", rows: 3, scope: "INPUT", mode: "INSERT", ariaLabel: "请求头", readonly })}${variableFieldMarkup({ label: "请求体（JSON 或文本）", id: "nodeApiBody", value: typeof config.body === "string" ? config.body : JSON.stringify(config.body || {}, null, 2), className: "form-control node-textarea", rows: 5, scope: "INPUT", mode: "INSERT", ariaLabel: "请求体", readonly })}<label><span>响应格式</span><select id="nodeApiResponseFormat" class="form-select" ${readonly ? "disabled" : ""}><option value="JSON" ${(config.response_format || "JSON") === "JSON" ? "selected" : ""}>JSON</option><option value="TEXT" ${(config.response_format || "JSON") === "TEXT" ? "selected" : ""}>Text</option></select></label>${inputMappingMarkup(config, node, readonly)}${outputMappingMarkup(config, readonly)}`;
    } else if (node.node_type === "LOOP") {
      fields = `${variableFieldMarkup({ label: "循环集合", id: "nodeLoopItems", value: config.items || "", placeholder: "{{ nodes.api_1.response }}", scope: "INPUT", ariaLabel: "循环集合", readonly })}<div class="form-row"><label><span>当前项变量名</span><input id="nodeLoopItemName" class="form-control" value="${escapeHtml(config.item_name || "item")}" ${readonly ? "readonly" : ""}></label><label><span>最大次数</span><input id="nodeLoopMaxIterations" class="form-control" type="number" min="1" max="1000" value="${escapeHtml(config.max_iterations ?? 10)}" ${readonly ? "readonly" : ""}></label></div>${inputMappingMarkup(config, node, readonly)}<p class="muted-copy">循环节点会限制集合处理次数并输出 items、count 与当前项变量。子流程执行将在后续运行器升级中开放；当前可用于把集合安全地传递给 Web 接口或规则节点。</p>${outputMappingMarkup(config, readonly)}`;
    } else {
      fields = node.node_type === "START"
        ? `<div class="system-node-note"><strong>开始节点</strong><p>系统自动注入必填图片变量 <code>{{ input.image_path }}</code>，无需在下方重复添加。外部接口把单张图片放在顶层 <code>image_path</code>；下方只维护业务校验参数，供配方 ROI 绑定和节点引用。当前一个场景执行一张图片，多图请逐张调用同一场景接口。</p></div>${parameterSectionMarkup({ id: "nodeStartInputParameters", title: "流程输入参数", description: "参数名称只用于页面展示，参数键用于配方绑定和节点引用。图片路径由系统保留，不会出现在此列表。", rows: schemaRows(config.inputs || []), kind: "SCHEMA", readonly, keyLabel: "参数键", valueLabel: "参数名称", keyPlaceholder: "例如 ocr_text", valuePlaceholder: "例如 OCR 校验文字", variableScope: "NONE" })}`
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
        <section class="node-config-panel" data-inspector-panel="INPUT" role="tabpanel"><div class="node-config-section"><header><strong>输入映射</strong><small>从开始输入或真正上游节点中选择变量，作为本节点输入。</small></header><div id="workflowNodeInputContent" class="stack-form compact-stack-form"></div></div></section>
        <section class="node-config-panel" data-inspector-panel="OUTPUT" role="tabpanel"><div class="node-config-section"><header><strong>输出声明</strong><small>为节点原始输出设置易读字段，供后续节点或接口返回使用。</small></header><div id="workflowNodeOutputContent" class="stack-form compact-stack-form"></div></div></section>
      </div>
      ${save}
    </div>`;
    setupInspectorTabs(target);
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
    const [scene, vlms, publishedScenes, visionVersions] = await Promise.all([
      request(`${api}/scenarios/${sceneId}`), request(`${api}/vlm-models`), request(`${api}/scenarios/published`), request(`${api}/vision-models/published`),
    ]);
    state.scene = scene;
    state.vlms = vlms;
    state.publishedScenes = publishedScenes;
    state.visionVersions = visionVersions;
    if (!preserveVersion || !scene.versions.some((version) => version.id === state.versionId)) {
      state.versionId = scene.versions.find((version) => version.status === "DRAFT")?.id || scene.versions[0]?.id || null;
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
    if (!await saveDirect({ quiet: true })) return;
    const form = new FormData(); form.append("image", file);
    startDirectConversation();
    appendConversation("user", "当前测试", `图片：${file.name}\n提示词已按左侧草稿保存。`);
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
    if (normalized === "OK") return "success";
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

  function workflowFinalOutputMarkup(result, finalOutput, errorMessage = "") {
    const label = workflowResultLabel(result || finalOutput?.result);
    const reason = finalOutput?.reason || finalOutput?.message || errorMessage || "流程已完成，可查看完整返回内容。";
    return `<div class="workflow-final-result-summary ${workflowResultBadgeClass(label)}"><span>${escapeHtml(label)}</span><p>${escapeHtml(reason)}</p></div><pre>${escapeHtml(formatWorkflowTraceValue(finalOutput))}</pre>`;
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
    byId("workflowTestFinalOutput").innerHTML = workflowFinalOutputMarkup(result, finalOutput, execution.error_message || "");
    byId("workflowTestTraceList").innerHTML = traces.length
      ? traces.map((trace, index) => `<details class="workflow-trace-card" ${index === traces.length - 1 ? "open" : ""}><summary><div><span class="workflow-trace-index">${index + 1}</span><div><strong>${escapeHtml(trace.node_name || trace.node_key || "节点")}</strong><small>${escapeHtml(nodeTypeLabel(trace.node_type || ""))} · ${escapeHtml(trace.node_key || "")}</small></div></div><div class="workflow-trace-summary-meta"><span class="workflow-trace-status ${workflowTraceStatus(trace.status)}">${escapeHtml(trace.status || "UNCERTAIN")}</span><b>${escapeHtml(trace.elapsed_ms ?? 0)} ms</b><i>⌄</i></div></summary><div class="workflow-trace-detail"><section><header><small>节点输入</small><span>完整参数</span></header><pre>${escapeHtml(formatWorkflowTraceValue(trace.input))}</pre></section><section><header><small>节点输出</small><span>完整结果</span></header><pre>${escapeHtml(formatWorkflowTraceValue(trace.output))}</pre></section></div></details>`).join("")
      : `<div class="empty-state compact-empty"><span>!</span><strong>流程未产生节点追踪</strong><p>${escapeHtml(execution.error_message || "请检查节点配置和连接关系。")}</p><pre>${escapeHtml(formatWorkflowTraceValue(finalOutput))}</pre></div>`;
  }

  function startWorkflowTestRun() {
    const panel = byId("workflowTestPanel");
    panel.hidden = false;
    document.querySelector(".workflow-stage-panel")?.classList.add("has-test");
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
    const payload = { node_key: nodeKeyFor(type), name: defaultNodeName(type), node_type: type, config_json: defaultNodeConfig(type), canvas_x: Math.round(position.x), canvas_y: Math.round(position.y), auto_connect: false };
    try {
      const node = await request(`${api}/scenarios/versions/${currentVersion().id}/nodes`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
      state.selectedNodeId = node.id;
      state.inspectorTab = "SETTINGS";
      state.inspectorCollapsed = false;
      await loadData();
      notify(`${nodeTypeLabel(type)}节点已加入画布，请配置后再连线。`);
    } catch (error) { notify(error.message, "danger"); }
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
    if (!isDraft() || !confirm("删除这条连线吗？")) return;
    try { await request(`${api}/scenarios/versions/${currentVersion().id}/edges/${edgeId}`, { method: "DELETE" }); await loadData(); } catch (error) { notify(error.message, "danger"); }
  }
  async function saveNodeConfig() {
    const node = nodeById(state.selectedNodeId); if (!node || !isDraft()) return;
    let config = deepCopy(node.config_json);
    try {
      const name = byId("nodeName")?.value.trim();
      if (!name) throw new Error("节点名称不能为空。");
      if (node.node_type === "START") {
        config.inputs = [
          { name: "image_path", label: "检测图片", type: "IMAGE", required: true },
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
        if (profile.supports_classification) {
          const expectedLabel = byId("nodeExpectedLabel").value.trim();
          if (!expectedLabel) throw new Error("分类模型需要填写期望类别。");
          config.expected_label = expectedLabel;
          delete config.expected_class;
          delete config.expected_min_count;
          delete config.expected_max_count;
          delete config.iou;
        } else if (profile.supports_objects) {
          config.expected_class = byId("nodeExpectedClass").value.trim() || null;
          config.expected_min_count = Number(byId("nodeExpectedMinCount").value);
          const expectedMaxText = byId("nodeExpectedMaxCount").value.trim();
          if (expectedMaxText) config.expected_max_count = Number(expectedMaxText);
          else delete config.expected_max_count;
          config.iou = Number(byId("nodeIou").value);
          delete config.expected_label;
        } else {
          delete config.expected_label;
          delete config.expected_class;
          delete config.expected_min_count;
          delete config.expected_max_count;
          delete config.iou;
        }
        config.input_mapping = readParameterRows("nodeInputParameters", "节点输入参数");
        config.context = config.input_mapping;
        config.output_mapping = readParameterRows("nodeOutputParameters", "节点输出参数");
      } else if (node.node_type === "IMAGE_CROP") {
        const bbox = byId("nodeCropBbox").value.trim();
        if (!bbox) throw new Error("请配置裁剪框 bbox，可引用上游训练模型的 objects.0.bbox。");
        const compatibilityError = cropBboxCompatibilityError(bbox);
        if (compatibilityError) throw new Error(compatibilityError);
        config.image_path = byId("nodeCropImagePath").value.trim() || "{{ input.image_path }}";
        config.bbox = bbox;
        config.padding_ratio = Number(byId("nodeCropPadding").value);
        if (!Number.isFinite(config.padding_ratio) || config.padding_ratio < 0 || config.padding_ratio > 1) throw new Error("边缘扩展比例必须在 0 到 1 之间。");
        config.output_mapping = readParameterRows("nodeOutputParameters", "节点输出参数");
      } else if (node.node_type === "RULE" || node.node_type === "IF") {
        config.actual = byId("nodeActual").value.trim(); config.operator = byId("nodeOperator").value; config.expected = byId("nodeExpected").value.trim();
        config.input_mapping = readParameterRows("nodeInputParameters", "节点输入参数");
        config.context = config.input_mapping;
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
      } else if (node.node_type === "LOOP") {
        config.items = byId("nodeLoopItems").value.trim(); config.item_name = byId("nodeLoopItemName").value.trim() || "item"; config.max_iterations = Number(byId("nodeLoopMaxIterations").value);
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
    if (!isDraft() || event.button !== 0) return;
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
    byId("designerVersionSelect").addEventListener("change", (event) => { state.versionId = Number(event.target.value); state.selectedNodeId = null; clearWorkflowTest(); renderAll(); });
    byId("designerSave").addEventListener("click", saveDesigner);
    byId("directSave").addEventListener("click", () => saveDirect());
    byId("designerClone").addEventListener("click", cloneVersion);
    byId("designerPublish").addEventListener("click", publishVersion);
    byId("designerApi").addEventListener("click", showSceneApiContract);
    byId("copySceneApiEndpoint").addEventListener("click", () => copySceneApiValue("endpoint"));
    byId("copySceneApiRequest").addEventListener("click", () => copySceneApiValue("request"));
    byId("copySceneApiResponse").addEventListener("click", () => copySceneApiValue("response"));
    byId("directTestImage").addEventListener("change", (event) => previewSelectedImage(event.target.files?.[0]));
    byId("directRunTest").addEventListener("click", runDirectTest);
    byId("directClearResult").addEventListener("click", clearDirectConversation);
    window.addEventListener("resize", fitDirectDesignerToViewport);
    byId("workflowZoomIn").addEventListener("click", () => setZoom(state.zoom + 0.1));
    byId("workflowZoomOut").addEventListener("click", () => setZoom(state.zoom - 0.1));
    byId("workflowZoomReset").addEventListener("click", () => setZoom(1));
    byId("workflowFitCanvas").addEventListener("click", fitWorkflowCanvas);
    byId("workflowAutoLayout").addEventListener("click", autoLayoutWorkflow);
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
    byId("workflowCanvasViewport").addEventListener("drop", (event) => { event.preventDefault(); const type = event.dataTransfer.getData("application/vision-node"); if (type) createWorkflowNode(type, event); });
    byId("workflowNodeLayer").addEventListener("pointerdown", (event) => { const port = event.target.closest("[data-output-node]"); if (port) { beginConnection(event, port.dataset.outputNode); return; } const element = event.target.closest("[data-node-id]"); if (element) beginNodeDrag(event, element); });
    byId("workflowNodeLayer").addEventListener("pointerup", (event) => { const port = event.target.closest("[data-input-node]"); if (port) { endConnection(event, port.dataset.inputNode); return; } endNodeDrag(); });
    window.addEventListener("pointermove", moveNodeDrag);
    window.addEventListener("pointerup", () => { if (state.connecting) { state.connecting = null; byId("workflowCanvasSurface").classList.remove("connecting"); } endNodeDrag(); });
    byId("workflowNodeLayer").addEventListener("click", (event) => { const element = event.target.closest("[data-node-id]"); if (!element || event.target.closest(".workflow-port")) return; state.selectedNodeId = Number(element.dataset.nodeId); openInspectorForNode(); renderWorkflowCanvas(); renderInspector(); });
    byId("workflowEdgeLayer").addEventListener("click", (event) => { const edge = event.target.closest("[data-edge-id]"); if (edge) deleteEdge(Number(edge.dataset.edgeId)); });
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
