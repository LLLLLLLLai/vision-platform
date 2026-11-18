const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const assert = require("node:assert/strict");
const { test } = require("node:test");
const root = path.resolve(__dirname, "..");
const designer = fs.readFileSync(path.join(root, "app/static/js/scene_designer.js"), "utf8");
const workspace = fs.readFileSync(path.join(root, "app/static/js/workspace.js"), "utf8");

function functionText(source, name, nextName) {
  const start = source.indexOf(name);
  assert.ok(start >= 0, name);
  const end = source.indexOf(nextName, start + name.length);
  assert.ok(end > start, nextName);
  return source.slice(start, end);
}

test("ROI binding is hydrated before details render and stale mappings disappear", () => {
  const state = { roiScenarioBindings: new Map([[2, { scenario_version_id: 8 }]]) };
  const context = vm.createContext({ state });
  vm.runInContext(functionText(workspace, "function hydrateRecipeBindings", "async function loadRecipe"), context);
  context.hydrateRecipeBindings({ rois: [{ id: 1, scenario_binding: { scenario_version_id: 5, scenario_name: "线束" } }, { id: 2, scenario_binding: null }] });
  assert.equal(state.roiScenarioBindings.get(1).scenario_name, "线束");
  assert.equal(state.roiScenarioBindings.has(2), false);
  const loading = functionText(workspace, "async function loadRecipe", "async function ensureProduct");
  assert.ok(loading.indexOf("hydrateRecipeBindings") < loading.indexOf("renderConfiguredObjects"));
});

test("scene filter matches either live draft or production, not unrelated history", () => {
  const context = vm.createContext({});
  vm.runInContext(functionText(workspace, "function recipeFamilyHasScene", "function renderLibrary"), context);
  const family = { draft: { scenarios: [{ scenario_id: 1 }] }, published: { scenarios: [{ scenario_id: 2 }] }, versions: [{ scenarios: [{ scenario_id: 3 }] }] };
  assert.equal(context.recipeFamilyHasScene(family, 1), true);
  assert.equal(context.recipeFamilyHasScene(family, "2"), true);
  assert.equal(context.recipeFamilyHasScene(family, 3), false);
  assert.equal(context.recipeFamilyHasScene(family, null), true);
});

function pickerContext() {
  const state = {
    visionVersions: [{ model_id: 1, model_name: "线束", version_id: 11, version: "2", status: "PUBLISHED" }, { model_id: 1, model_name: "线束", version_id: 10, version: "1", status: "ARCHIVED" }],
    vlms: [{ id: 2, name: "VLM", enabled: true }],
    vlmVersions: [{ model_id: 2, id: 20, version: 1 }],
  };
  const context = vm.createContext({ state, escapeHtml: String, selectedVisionModelVersion: (id) => state.visionVersions.find((item) => item.version_id === Number(id)) });
  vm.runInContext(functionText(designer, "function modelPickerMarkup", "  function captureVisionInspector"), context);
  return context;
}

test("model selector has model and version dropdowns and retains fixed historical versions", () => {
  const context = pickerContext();
  const html = context.modelPickerMarkup("VISION_MODEL", { model_version_id: 10 }, false);
  assert.equal((html.match(/<select /g) || []).length, 2);
  assert.match(html, /value="10" selected/);
  assert.match(html, /最新版本（自动跟随）/);
  assert.match(html, /（历史）/);
  const vlm = context.modelPickerMarkup("VLM", { vlm_model_id: 2, vlm_version_mode: "FIXED", vlm_version_id: 20 }, false);
  assert.match(vlm, /value="20" selected/);
});

test("unavailable fixed models cannot silently change to latest", () => {
  const context = pickerContext();
  const html = context.modelPickerMarkup("VISION_MODEL", { vision_model_id: 90, model_version_mode: "FIXED", model_version_id: 99 }, false);
  assert.match(html, /value="90" selected/);
  assert.match(html, /value="99" selected/);
  assert.match(html, /原指定版本/);
  const picker = { dataset: { modelPicker: "VISION_MODEL" }, querySelector: (selector) => ({ value: selector === "[data-model-name]" ? "90" : "99" }) };
  const config = context.modelPickerValue(picker);
  assert.equal(config.model_version_mode, "FIXED");
  assert.equal(config.model_version_id, 99);
});

test("latest choice serializes logical model; fixed choice retains exact version", () => {
  const context = pickerContext();
  const picker = { dataset: { modelPicker: "VISION_MODEL" }, querySelector: (selector) => ({ value: selector === "[data-model-name]" ? "1" : "LATEST" }) };
  let config = context.modelPickerValue(picker);
  assert.equal(config.vision_model_id, 1);
  assert.equal(config.model_version_mode, "LATEST");
  assert.equal(config.model_version_id, 11);
  picker.querySelector = (selector) => ({ value: selector === "[data-model-name]" ? "1" : "10" });
  config = context.modelPickerValue(picker);
  assert.equal(config.model_version_mode, "FIXED");
  assert.equal(config.model_version_id, 10);
});

test("collection-to-detection saves before rendering, and rollback works on failure", async () => {
  let handler;
  let rejectSave = false;
  const version = { definition_json: { run_mode: "COLLECT_ONLY" } };
  const modes = [];
  const field = { disabled: false, value: "DETECT", addEventListener: (_, callback) => { handler = callback; } };
  const context = vm.createContext({
    byId: () => field, isDraft: () => true, currentVersion: () => version,
    state: { scene: { mode: "WORKFLOW" } }, collectionOnly: () => version.definition_json.run_mode === "COLLECT_ONLY",
    deepCopy: (value) => JSON.parse(JSON.stringify(value)),
    saveRunMode: async () => { if (rejectSave) throw Error("network"); modes.push("saved:" + version.definition_json.run_mode); },
    renderAll: () => modes.push("rendered:" + version.definition_json.run_mode), notify: () => {},
  });
  const start = designer.indexOf('byId("designerRunMode").addEventListener("change"');
  const end = designer.indexOf('    byId("collectionRefresh")', start);
  vm.runInContext(designer.slice(start, end), context);
  await handler({ target: field });
  assert.deepEqual(modes, ["saved:DETECT", "rendered:DETECT"]);
  assert.equal(version.definition_json.run_mode, "DETECT");
  rejectSave = true;
  field.value = "COLLECT_ONLY";
  await handler({ target: field });
  assert.equal(version.definition_json.run_mode, "DETECT");
});
