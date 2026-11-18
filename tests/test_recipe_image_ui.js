const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const assert = require("node:assert/strict");
const { test } = require("node:test");
const source = fs.readFileSync(path.join(__dirname, "../app/static/js/workspace.js"), "utf8");

function section(start, end) {
  return source.slice(source.indexOf(start), source.indexOf(end, source.indexOf(start)));
}

test("recipe upload errors describe HTTP and validation errors rather than invalid JSON", async () => {
  for (const [status, body, message] of [[500, null, /HTTP 500/], [413, null, /图片过大/], [422, {detail: [{loc: ["body", "name"], msg: "名称为空"}]}, /name：名称为空/]]) {
    const context = vm.createContext({fetch: async () => ({status, ok: false, json: async () => { if (!body) throw new Error("Invalid JSON"); return body; }})});
    vm.runInContext(section("async function request(", "function escapeHtml("), context);
    await assert.rejects(context.request("/image"), message);
  }
});

test("editing published image creates or reuses draft and updates editor URL", async () => {
  const calls = [];
  const state = {recipe: {id: 7, status: "PUBLISHED"}, editorReadOnly: false};
  const context = vm.createContext({
    state, api: "/api/v1", URL,
    recipeFields: () => ({lineCode: "L1", materialCode: "M1", processCode: "OP1", cameraCode: "CAMERA1"}),
    generatedRecipe: () => ({code: "RECIPE", name: "配方"}),
    ensureProduct: async () => ({id: 1}), ensureStation: async () => ({id: 1}),
    request: async (url, options) => { calls.push([url, options.method]); return {id: 8}; },
    loadData: async (id) => { state.recipe = {id, status: "DRAFT"}; },
    window: {location: {href: "http://localhost/recipes/editor?recipe_id=7&mode=edit"},
      history: {replaceState: (...args) => calls.push(args[2])}},
  });
  vm.runInContext(section("async function ensureWorkingRecipe(", "async function saveRecipe("), context);
  assert.equal((await context.ensureWorkingRecipe()).id, 8);
  assert.deepEqual(calls.slice(0, 2), [["/api/v1/configuration/recipes/7/draft", "POST"], ["/api/v1/configuration/recipes/8", "PUT"]]);
  assert.equal(calls[2], "/recipes/editor?recipe_id=8");
  calls.length = 0;
  await context.ensureWorkingRecipe();
  assert.deepEqual(calls, [["/api/v1/configuration/recipes/8", "PUT"]]);
  state.editorReadOnly = true;
  await assert.rejects(context.ensureWorkingRecipe(), /只能查看/);
});

test("image replacement confirms configuration reset and allows same-file retry on failure", async () => {
  for (const confirmed of [false, true]) {
    const calls = [];
    const fields = {baseImageInput: {value: "image.png"}, emptyImageInput: {value: "image.png"}};
    const context = vm.createContext({
      state: {recipe: {id: 8, base_image_url: "old.png", rois: [{}]}, imageView: {}}, api: "/api/v1",
      byId: (id) => fields[id],
      confirmWorkspaceAction: async (title, message) => { assert.match(message, /历史检测记录/); return confirmed; },
      ensureWorkingRecipe: async () => { calls.push("draft"); return {id: 8}; },
      FormData: class { append() {} },
      request: async () => { throw new Error("保存失败"); },
      notify: (message) => calls.push(message),
    });
    vm.runInContext(section("async function uploadBaseImage(", "function applyImageTransform("), context);
    await context.uploadBaseImage({name: "image.png"});
    assert.equal(fields.baseImageInput.value, "");
    assert.equal(fields.emptyImageInput.value, "");
    assert.deepEqual(calls, confirmed ? ["draft", "保存失败"] : []);
  }
});
