const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const assert = require("node:assert/strict");
const { test } = require("node:test");
const source = fs.readFileSync(path.join(__dirname, "../app/static/js/scene_designer.js"), "utf8");

function functionSource(name, nextName) {
  const start = source.indexOf(name);
  const end = source.indexOf(nextName, start + name.length);
  assert.ok(start >= 0 && end > start);
  return source.slice(start, end);
}

function fixture(options = {}) {
  const calls = [];
  const messages = [];
  const node = { id: 12, node_type: options.nodeType || "VLM", config_json: {} };
  const version = { id: 7, status: options.status || "DRAFT" };
  const state = { scene: { mode: options.mode || "WORKFLOW" }, selectedNodeId: options.noNode ? null : node.id, publishing: false };
  const header = { disabled: false };
  const fields = {
    nodeName: { value: options.emptyName ? "" : "检测节点" },
    nodeVlmMode: { value: "CUSTOM" },
  };
  let pendingSave;
  const context = vm.createContext({
    state, api: "/api/v1",
    byId: (id) => fields[id] || {},
    isDraft: () => version.status === "DRAFT",
    currentVersion: () => version,
    nodeById: (id) => id === node.id ? node : null,
    closeSubflowCanvas: () => {},
    deepCopy: (value) => JSON.parse(JSON.stringify(value)),
    readVlmInspectorConfig: () => ({ vlm_model_id: 2, prompt: "检查线束是否存在" }),
    readSchemaParameterRows: () => [{ name: "type_name", label: "型号" }],
    readParameterRows: () => ({ result: "{{ nodes.inspect.result }}" }),
    collectionOnly: () => Boolean(options.collection),
    saveRunMode: async () => { calls.push("save-mode"); },
    saveDirect: async (payload) => {
      calls.push("save-direct");
      assert.equal(payload.quiet, true);
      return !options.failSave;
    },
    loadData: async () => { calls.push("reload"); },
    notify: (message) => messages.push(message),
    updateHeader: () => { header.disabled = state.publishing; },
    runDesignerPreflight: async () => {
      calls.push("preflight");
      return options.unavailable ? null : { ready: !options.invalid, affected_roi_count: 2 };
    },
    confirm: () => { calls.push("confirm"); return !options.cancel; },
    window: { bootstrap: { Modal: { getOrCreateInstance: () => ({ show: () => calls.push("issues") }) } } },
    request: async (url, payload) => {
      if (url.endsWith("/publish")) {
        calls.push("publish");
        if (options.failPublish) throw new Error("发布请求失败");
        version.status = "PUBLISHED";
        return { api_endpoint_path: "/api/scene/test" };
      }
      calls.push("save-node");
      assert.equal(JSON.parse(payload.body).name, "检测节点");
      if (options.delaySave) await new Promise((resolve) => { pendingSave = resolve; });
      if (options.failSave) throw new Error("节点保存失败");
      return { id: node.id };
    },
  });
  vm.runInContext([
    functionSource("async function saveDesigner(", "async function cloneVersion("),
    functionSource("async function publishVersion(", "function previewSelectedImage("),
    functionSource("async function saveNodeConfig(", "async function deleteSelectedNode("),
  ].join("\n"), context);
  return { context, calls, messages, state, version, header, release: () => pendingSave() };
}

for (const nodeType of ["VLM", "START", "END"]) {
  test("first publish saves selected " + nodeType + " and publishes without reopening", async () => {
    const result = fixture({ nodeType });
    assert.equal(await result.context.publishVersion(), true);
    assert.deepEqual(result.calls, ["save-mode", "save-node", "reload", "preflight", "confirm", "publish", "reload"]);
    assert.equal(result.version.status, "PUBLISHED");
    assert.equal(result.header.disabled, false);
    assert.equal(result.messages.length, 1);
    assert.match(result.messages[0], /场景版本已发布/);
  });
}

test("node save reports success and validation failure explicitly", async () => {
  const valid = fixture();
  assert.equal(await valid.context.saveNodeConfig(), true);
  assert.match(valid.messages[0], /节点配置已保存/);
  const invalid = fixture({ emptyName: true });
  assert.equal(await invalid.context.saveNodeConfig(), false);
  assert.equal(invalid.calls.length, 0);
});

test("invalid or failed save stops publish, preserves draft and unlocks UI", async () => {
  for (const options of [{ failSave: true }, { emptyName: true }]) {
    const result = fixture(options);
    assert.equal(await result.context.publishVersion(), false);
    assert.ok(!result.calls.includes("preflight"));
    assert.ok(!result.calls.includes("publish"));
    assert.equal(result.version.status, "DRAFT");
    assert.equal(result.state.publishing, false);
    assert.equal(result.header.disabled, false);
    assert.equal(result.messages.length, 1);
  }
});

test("preflight failure, missing service, canceled confirmation and publish errors unlock UI", async () => {
  for (const options of [{ invalid: true }, { unavailable: true }, { cancel: true }, { failPublish: true }]) {
    const result = fixture(options);
    assert.equal(await result.context.publishVersion(), false);
    assert.equal(result.version.status, "DRAFT");
    assert.equal(result.state.publishing, false);
    assert.equal(result.header.disabled, false);
    if (!options.failPublish) assert.ok(!result.calls.includes("publish"));
  }
});

test("duplicate publish clicks are ignored while saving", async () => {
  const result = fixture({ delaySave: true });
  const first = result.context.publishVersion();
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(result.header.disabled, true);
  assert.equal(await result.context.publishVersion(), false);
  result.release();
  assert.equal(await first, true);
  assert.equal(result.calls.filter((call) => call === "publish").length, 1);
});

test("direct VLM, collection and workflow with no selected node still publish", async () => {
  for (const options of [{ mode: "VLM_DIRECT" }, { collection: true }, { noNode: true }]) {
    const result = fixture(options);
    assert.equal(await result.context.publishVersion(), true);
    assert.ok(result.calls.includes("publish"));
    assert.equal(result.messages.length, 1);
  }
  const result = fixture({ status: "PUBLISHED" });
  assert.equal(await result.context.publishVersion(), false);
  assert.equal(result.calls.length, 0);
});
