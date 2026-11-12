const test = require("node:test");
const assert = require("node:assert/strict");
const { augmentationNumber, augmentationBatches, formatRequestError, annotationColor } = require("../app/static/js/dataset_helpers.js");

test("FastAPI validation errors display field names and constraints, not object Object", () => {
  const message = formatRequestError({ detail: [
    { type: "less_than_equal", loc: ["body", "rotation_max_degrees"], msg: "Input should be less than or equal to 45", ctx: { le: 45 } },
    { type: "greater_than_equal", loc: ["body", "brightness_min"], ctx: { ge: 0.2 } },
  ] }, 422);
  assert.equal(message, "旋转最大角度：不能大于 45；亮度最小值：不能小于 0.2");
  assert.ok(!message.includes("[object Object]"));
});

test("nested, string, empty and unknown response errors remain readable", () => {
  assert.equal(formatRequestError({ detail: { message: "源图片不存在" } }, 400), "源图片不存在");
  assert.equal(formatRequestError({ error: { message: "服务忙" } }, 503), "服务忙");
  assert.equal(formatRequestError({ detail: "标注不合法" }, 400), "标注不合法");
  assert.match(formatRequestError({}, 502), /HTTP 502/);
  assert.match(formatRequestError(null, 500), /HTTP 500/);
  assert.match(formatRequestError({ detail: { reason: "磁盘不足" } }, 500), /磁盘不足/);
});

test("augmentation fields reject blanks, nonfinite values, fractions and out-of-range input", () => {
  for (const value of ["", " ", null, undefined, "abc", Infinity, -46, 46]) {
    assert.throws(() => augmentationNumber(value, "rotation_max_degrees"), /旋转最大角度/);
  }
  for (const value of [0, 21, 1.5]) assert.throws(() => augmentationNumber(value, "copies_per_item"));
  assert.throws(() => augmentationNumber(2147483648, "seed"));
  assert.throws(() => augmentationNumber(0.1, "brightness_min"));
  assert.throws(() => augmentationNumber(9, "gaussian_blur_radius_max"));
  assert.equal(augmentationNumber("-45", "rotation_min_degrees"), -45);
  assert.equal(augmentationNumber("45", "rotation_max_degrees"), 45);
  assert.equal(augmentationNumber("0", "saturation_min"), 0);
  assert.equal(augmentationNumber("2147483647", "seed"), 2147483647);
});

test("2634 sources are split without dropping images or exceeding the batch budget", () => {
  const ids = Array.from({ length: 2634 }, (_, index) => index + 1);
  for (const copies of [1, 3, 20]) {
    const batches = augmentationBatches(ids, copies);
    assert.deepEqual(batches.flat(), ids);
    assert.ok(batches.every((batch) => batch.length * copies <= 200));
    assert.equal(batches.length, Math.ceil(ids.length / Math.floor(200 / copies)));
  }
  assert.deepEqual(augmentationBatches([], 1), []);
});

test("category colors are stable, saturated, translucent-ready and not black or white", () => {
  const labels = ["FU5", "FU6", "FU7", "线束", "连接器", "螺钉", "保险丝"];
  const colors = labels.map(annotationColor);
  assert.equal(new Set(colors).size, labels.length);
  for (const [index, color] of colors.entries()) {
    assert.equal(color, annotationColor(labels[index]));
    const [, hue, saturation, lightness] = color.match(/^hsl\((\d+), (\d+)%, (\d+)%\)$/);
    assert.ok(Number(hue) >= 0 && Number(hue) < 360);
    assert.ok(Number(saturation) >= 70 && Number(saturation) <= 84);
    assert.ok(Number(lightness) >= 45 && Number(lightness) <= 54);
  }
  assert.equal(annotationColor(" 线束 "), annotationColor("线束"));
});
