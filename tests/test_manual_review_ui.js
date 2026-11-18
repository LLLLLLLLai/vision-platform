const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const assert = require("node:assert/strict");
const { test } = require("node:test");
const script = fs.readFileSync(path.join(__dirname, "../app/static/js/workspace.js"), "utf8");
const start = script.indexOf('byId("detectionRecordDetailContent").addEventListener("click"');
const end = script.indexOf('byId("recordImageLightbox").addEventListener', start);
const handlerScript = script.slice(start, end);

function setup(options = {}) {
  const calls = [];
  const feedback = { textContent: "" };
  const note = { value: " 人工检查 " };
  const section = {
    dataset: { selectedManualVerdict: options.verdict || "" },
    querySelector: (selector) => selector === "[data-manual-review-note]" ? note : feedback,
    querySelectorAll: () => choices,
  };
  const choices = ["OK", "NG", "UNCERTAIN"].map((verdict) => ({
    dataset: { manualVerdict: verdict },
    closest: (selector) => selector === "[data-manual-review-execution]" ? section : null,
  }));
  const save = {
    dataset: { saveManualReview: "12" }, disabled: false,
    closest: (selector) => selector === "[data-manual-review-execution]" ? section : null,
  };
  let handler;
  const state = { inspectionReports: { old: true } };
  const context = vm.createContext({
    state, api: "/api/v1",
    byId: (id) => id === "detectionRecordDetailContent" ? {
      dataset: { recordId: "7" },
      addEventListener: (name, callback) => { handler = callback; },
    } : null,
    notify: () => {},
    openDetectionRecordDetail: async (id) => calls.push({ recordId: id }),
    request: async (url, requestOptions) => {
      calls.push({ url, ...requestOptions });
      if (options.fail) throw new Error("连接失败");
    },
  });
  vm.runInContext(handlerScript, context);
  const click = (kind, verdict) => handler({
    target: { closest: (selector) => {
      if (selector === "[data-manual-verdict]") return section;
      if (selector === "button[data-manual-verdict]" && kind === "choice") return choices.find((choice) => choice.dataset.manualVerdict === verdict);
      if (selector === "[data-save-manual-review]" && kind === "save") return save;
      return null;
    } },
  });
  return { click, calls, section, save, feedback, state };
}

for (const verdict of ["OK", "NG", "UNCERTAIN"]) {
  test("manual save sends and reloads " + verdict, async () => {
    const fixture = setup();
    await fixture.click("choice", verdict);
    assert.equal(fixture.calls.length, 0);
    await fixture.click("note");
    assert.equal(fixture.section.dataset.selectedManualVerdict, verdict);
    await fixture.click("save");
    assert.equal(fixture.calls.length, 2);
    assert.equal(fixture.calls[0].method, "PUT");
    assert.equal(fixture.calls[0].url, "/api/v1/scenarios/executions/12/manual-review");
    assert.deepEqual(JSON.parse(fixture.calls[0].body), { verdict, note: "人工检查" });
    assert.equal(fixture.calls[1].recordId, 7);
    assert.equal(fixture.save.disabled, false);
    assert.equal(fixture.state.inspectionReports, null);
  });
}

test("save without verdict reports validation and does not send", async () => {
  const fixture = setup();
  await fixture.click("save");
  assert.equal(fixture.calls.length, 0);
  assert.match(fixture.feedback.textContent, /请先选择/);
});

test("failed save shows inline error and supports retry", async () => {
  const fixture = setup({ verdict: "NG", fail: true });
  await fixture.click("save");
  assert.match(fixture.feedback.textContent, /连接失败/);
  assert.equal(fixture.save.disabled, false);
  assert.equal(fixture.section.dataset.selectedManualVerdict, "NG");
  assert.equal(fixture.calls.length, 1);
  assert.notEqual(fixture.state.inspectionReports, null);
});

test("manual verdict section does not use option attribute", () => {
  const markup = script.slice(script.indexOf("function renderManualReview("), script.indexOf("function renderDetectionRecordDetail("));
  assert.match(markup, /data-selected-manual-verdict=/);
  assert.doesNotMatch(markup, /<section[^>]* data-manual-verdict=/);
});
