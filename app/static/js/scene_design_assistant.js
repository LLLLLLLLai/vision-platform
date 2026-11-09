(() => {
  const byId = (id) => document.getElementById(id);
  const sceneId = Number(byId("sceneDesigner").dataset.sceneId);
  const endpoint = `/api/v1/scenarios/${sceneId}/design-assistant`;
  const escapeHtml = (value) => String(value ?? "").replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;").replaceAll('"', "&quot;").replaceAll("'", "&#039;");
  let design = null;
  let messages = [];
  let previewUrl = null;
  let busy = false;
  let models = [];
  const status = (message, error = false) => {
    byId("sceneAssistantStatus").textContent = message;
    byId("sceneAssistantStatus").classList.toggle("error", error);
  };
  const request = async (url, options = {}) => {
    const response = await fetch(url, options);
    const payload = await response.json();
    if (!response.ok) throw new Error(typeof payload.detail === "string" ? payload.detail : "请求参数无效，请检查图片、要求和模型配置。");
    return payload;
  };
  function setBusy(value) {
    busy = value;
    for (const id of ["sceneAssistantGenerate", "sceneAssistantReset", "sceneAssistantModel", "sceneAssistantImage", "sceneAssistantRequirements"]) byId(id).disabled = value;
    for (const id of ["sceneAssistantPrompt", "sceneAssistantDetectionModel"]) if (byId(id)) byId(id).disabled = value;
    byId("sceneAssistantApply").disabled = value || !design;
    byId("sceneAssistantGenerate").textContent = value ? "正在生成…" : design ? "发送补充要求" : "生成设计";
  }
  function renderMessages() {
    byId("sceneAssistantMessages").innerHTML = messages.map((message) => `<div class="scene-assistant-message ${message.role}"><strong>${message.role === "user" ? "我的要求" : "设计助手"}</strong>\n${escapeHtml(message.content)}</div>`).join("");
    byId("sceneAssistantMessages").scrollTop = byId("sceneAssistantMessages").scrollHeight;
  }
  function renderDesign() {
    const fields = design.input_fields || [];
    const fieldTable = fields.length ? `<h3 class="fs-6 mt-3">可传入的校验参数</h3><table class="table"><thead><tr><th>字段</th><th>名称</th><th>示例 / 默认值</th></tr></thead><tbody>${fields.map((field) => `<tr><td>${escapeHtml(field.name)}</td><td>${escapeHtml(field.label)}</td><td>${escapeHtml(typeof field.default === "object" ? JSON.stringify(field.default) : field.default)}</td></tr>`).join("")}</tbody></table>` : "";
    const typeNames = { START: "开始", END: "结束", VLM: "VLM检测", VISION_MODEL: "训练模型", RULE: "规则判断", IMAGE_CROP: "图片裁剪", IMAGE_ANNOTATE: "图片画框" };
    const content = design.mode === "VLM_DIRECT"
      ? `<label class="d-block mt-3">检测使用的 VLM<select class="form-select" id="sceneAssistantDetectionModel">${models.map((model) => `<option value="${model.id}" ${model.id === design.primary_vlm_model_id ? "selected" : ""}>${escapeHtml(model.name)}</option>`).join("")}</select></label><div class="d-flex justify-content-between align-items-center my-2"><strong>检测提示词（可修改）</strong><button class="btn btn-sm btn-outline-secondary" id="sceneAssistantCopy" type="button">复制提示词</button></div><textarea class="form-control" id="sceneAssistantPrompt" maxlength="8000">${escapeHtml(design.prompt_template)}</textarea>`
      : `<h3 class="fs-6 mt-3">流程节点</h3><table class="table"><thead><tr><th>顺序 / 节点</th><th>类型 / 配置</th></tr></thead><tbody>${design.nodes.map((node, index) => `<tr><td>${index + 1}. ${escapeHtml(node.name)}<br><small>${escapeHtml(node.node_key)}</small></td><td>${escapeHtml(typeNames[node.node_type] || node.node_type)}<details><summary>查看参数</summary><pre>${escapeHtml(JSON.stringify(node.config_json, null, 2))}</pre></details></td></tr>`).join("")}</tbody></table><div class="small text-muted">连线：${design.edges.map((edge) => `${escapeHtml(edge.source_node_key)} → ${escapeHtml(edge.target_node_key)}`).join("；")}</div>`;
    byId("sceneAssistantDesign").innerHTML = `<div class="scene-assistant-explanation">${escapeHtml(design.explanation)}</div>${content}${fieldTable}`;
    byId("sceneAssistantPrompt")?.addEventListener("input", (event) => { design.prompt_template = event.target.value; });
    byId("sceneAssistantDetectionModel")?.addEventListener("change", (event) => { design.primary_vlm_model_id = Number(event.target.value); });
    byId("sceneAssistantCopy")?.addEventListener("click", async () => {
      try { await navigator.clipboard.writeText(design.prompt_template); status("提示词已复制。"); }
      catch { byId("sceneAssistantPrompt").select(); status("浏览器不支持自动复制，已选中提示词，请按 Ctrl+C。"); }
    });
  }
  function resetConversation() {
    if (busy) return;
    design = null; messages = []; renderMessages(); setBusy(false);
    byId("sceneAssistantDesign").textContent = "填写检测要求后生成设计。生成内容不会自动用于生产。";
    status("已清空对话，可重新生成。");
  }
  byId("openSceneAssistant").addEventListener("click", async () => {
    bootstrap.Modal.getOrCreateInstance(byId("sceneAssistantModal")).show();
    try {
      models = (await request("/api/v1/vlm-models")).filter((model) => model.enabled);
      const previous = byId("sceneAssistantModel").value;
      byId("sceneAssistantModel").innerHTML = '<option value="">请选择已配置的 VLM 接口</option>' + models.map((model) => `<option value="${model.id}">${escapeHtml(model.name)}</option>`).join("");
      byId("sceneAssistantModel").value = previous || byId("directPrimaryVlm")?.value || String(models[0]?.id || "");
      if (!models.length) status("尚无可用VLM接口，请先到“VLM模型配置”中创建并启用。", true);
    } catch (error) { status(error.message, true); }
  });
  byId("sceneAssistantReset").addEventListener("click", resetConversation);
  byId("sceneAssistantImage").addEventListener("change", () => {
    if (previewUrl) URL.revokeObjectURL(previewUrl);
    const file = byId("sceneAssistantImage").files[0];
    previewUrl = file ? URL.createObjectURL(file) : null;
    byId("sceneAssistantImagePreview").hidden = !file;
    byId("sceneAssistantImagePreview").src = previewUrl || "";
  });
  byId("sceneAssistantForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    if (busy) return;
    const file = byId("sceneAssistantImage").files[0];
    const requirements = byId("sceneAssistantRequirements").value.trim();
    if (!file || file.size > 20 * 1024 * 1024 || !requirements) { status("请选择不超过20MB的图片，并填写检测要求。", true); return; }
    if (messages.length >= 12) { status("当前对话已达6轮，请保存草稿或清空对话后继续。", true); return; }
    const form = new FormData();
    form.append("image", file); form.append("vlm_model_id", byId("sceneAssistantModel").value);
    form.append("requirements", requirements);
    form.append("conversation_json", JSON.stringify({ messages, previous_design: design }));
    setBusy(true); status("正在生成并检查设计，原场景不会被修改…");
    try {
      const result = await request(endpoint, { method: "POST", body: form });
      design = result.design;
      messages.push({ role: "user", content: requirements }, { role: "assistant", content: design.explanation || "设计已生成，请查看右侧预览。" });
      renderMessages(); renderDesign(); status(result.message);
      byId("sceneAssistantRequirements").value = "";
    } catch (error) { status(error.message, true); }
    finally { setBusy(false); }
  });
  byId("sceneAssistantApply").addEventListener("click", async () => {
    if (!design || busy) return;
    setBusy(true); status("正在创建独立草稿…");
    try {
      const result = await request(`${endpoint}/apply`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(design) });
      bootstrap.Modal.getInstance(byId("sceneAssistantModal")).hide();
      document.dispatchEvent(new CustomEvent("scene-design-applied", { detail: result }));
      resetConversationAfterApply();
    } catch (error) { status(error.message, true); }
    finally { setBusy(false); }
  });
  function resetConversationAfterApply() { design = null; messages = []; renderMessages(); byId("sceneAssistantDesign").textContent = "草稿已创建。可重新生成其他方案。"; }
})();
