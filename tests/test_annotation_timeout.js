const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const assert = require("node:assert/strict");
const { test } = require("node:test");
const source = fs.readFileSync(path.join(__dirname, "../app/static/js/dataset_helpers.js"), "utf8");

function fixture() {
  const timers = new Map();
  let nextTimer = 0;
  const context = vm.createContext({
    module: { exports: {} }, AbortController, AbortSignal: {},
    setTimeout: (callback, delay) => { const timer = ++nextTimer; timers.set(timer, { callback, delay }); return timer; },
    clearTimeout: (timer) => timers.delete(timer),
  });
  vm.runInContext(source, context);
  return {
    ...context.module.exports, timers,
    expire: () => { const [timer, entry] = [...timers][0]; timers.delete(timer); entry.callback(); },
  };
}

test("annotation save succeeds without AbortSignal.timeout and clears its timer", async () => {
  const helpers = fixture();
  let usedSignal;
  const result = await helpers.withAnnotationSaveTimeout(async (signal) => {
    usedSignal = signal;
    assert.equal([...helpers.timers.values()][0].delay, 30000);
    return { annotation_status: "LABELED" };
  });
  assert.equal(result.annotation_status, "LABELED");
  assert.equal(usedSignal.aborted, false);
  assert.equal(helpers.timers.size, 0);
});

test("network, validation and synchronous errors are preserved with no leaked timers", async () => {
  for (const message of ["网络异常", "类别不合法"]) {
    const helpers = fixture();
    const originalError = new Error(message);
    await assert.rejects(helpers.withAnnotationSaveTimeout(() => { throw originalError; }), (error) => error === originalError);
    assert.equal(helpers.timers.size, 0);
  }
});

test("timeout aborts pending save and reports a readable retryable error", async () => {
  const helpers = fixture();
  let usedSignal;
  const pending = helpers.withAnnotationSaveTimeout((signal) => {
    usedSignal = signal;
    return new Promise((resolve, reject) => signal.addEventListener("abort", () => reject(new Error("aborted")), { once: true }));
  });
  helpers.expire();
  await assert.rejects(pending, (error) => error.name === "TimeoutError" && /标注保存超时/.test(error.message));
  assert.equal(usedSignal.aborted, true);
  assert.equal(helpers.timers.size, 0);
});

test("a swallowed response body abort cannot incorrectly mark an annotation saved", async () => {
  const helpers = fixture();
  const pending = helpers.withAnnotationSaveTimeout((signal) => new Promise((resolve) => {
    signal.addEventListener("abort", () => resolve({}), { once: true });
  }));
  helpers.expire();
  await assert.rejects(pending, /标注保存超时/);
  assert.equal(helpers.timers.size, 0);
});

test("timed out autosave keeps annotation dirty and retries with a fresh signal", async () => {
  const helpers = fixture();
  const payload = { boxes: [{ label: "线束", x: 0.1, y: 0.2, width: 0.3, height: 0.4 }] };
  const writes = [];
  const signals = [];
  const states = [];
  let failing = true;
  const saver = helpers.createAnnotationAutosave({
    read: () => payload,
    write: (value) => helpers.withAnnotationSaveTimeout((signal) => {
      writes.push(value);
      signals.push(signal);
      if (!failing) return Promise.resolve();
      return new Promise((resolve, reject) => signal.addEventListener("abort", () => reject(new Error("aborted")), { once: true }));
    }),
    onState: (status) => states.push(status),
  });
  saver.change();
  const pending = saver.flush();
  await Promise.resolve();
  helpers.expire();
  await assert.rejects(pending, /标注保存超时/);
  assert.equal(saver.dirty(), true);
  assert.equal(states.at(-1), "error");
  failing = false;
  await saver.flush();
  assert.deepEqual(writes, [payload, payload]);
  assert.equal(signals[0].aborted, true);
  assert.equal(signals[1].aborted, false);
  assert.equal(saver.dirty(), false);
  assert.equal(states.at(-1), "saved");
  saver.dispose();
  assert.equal(helpers.timers.size, 0);
});
