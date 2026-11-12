(() => {
  const fields = {
    copies_per_item: ["每张原图生成数量", 1, 20, true],
    seed: ["随机种子", 0, 2147483647, true],
    rotation_min_degrees: ["旋转最小角度", -45, 45],
    rotation_max_degrees: ["旋转最大角度", -45, 45],
    brightness_min: ["亮度最小值", 0.2, 2.5],
    brightness_max: ["亮度最大值", 0.2, 2.5],
    contrast_min: ["对比度最小值", 0.2, 2.5],
    contrast_max: ["对比度最大值", 0.2, 2.5],
    saturation_min: ["饱和度最小值", 0, 2.5],
    saturation_max: ["饱和度最大值", 0, 2.5],
    gaussian_blur_radius_min: ["模糊最小值", 0, 8],
    gaussian_blur_radius_max: ["模糊最大值", 0, 8],
    gaussian_noise_std_min: ["噪声最小值", 0, 80],
    gaussian_noise_std_max: ["噪声最大值", 0, 80],
  };

  function augmentationNumber(value, field) {
    const [label, minimum, maximum, integer] = fields[field];
    if (value === null || value === undefined || String(value).trim() === "") throw new Error(`${label}不能为空。`);
    const number = Number(value);
    if (!Number.isFinite(number) || (integer && !Number.isInteger(number)) || number < minimum || number > maximum) {
      throw new Error(`${label}需为 ${minimum} 到 ${maximum} 的${integer ? "整数" : "数字"}。`);
    }
    return number;
  }

  function augmentationBatches(sourceIds, copiesPerItem, imageLimit = 200) {
    const copies = augmentationNumber(copiesPerItem, "copies_per_item");
    const size = Math.max(1, Math.floor(imageLimit / copies));
    const batches = [];
    for (let offset = 0; offset < sourceIds.length; offset += size) batches.push(sourceIds.slice(offset, offset + size));
    return batches;
  }

  function formatRequestError(payload, status) {
    const fallback = `请求失败（HTTP ${status}），请检查服务日志。`;
    function describe(value) {
      if (typeof value === "string") return value;
      if (Array.isArray(value)) return value.slice(0, 5).map(describe).filter(Boolean).join("；");
      if (!value || typeof value !== "object" || !Object.keys(value).length) return "";
      if (value.loc) {
        const path = value.loc.filter((part) => !["body", "query", "path"].includes(part));
        const name = fields[path[0]]?.[0] || path.join(".") || "参数";
        const context = value.ctx || {};
        const messages = {
          greater_than_equal: `不能小于 ${context.ge}`,
          less_than_equal: `不能大于 ${context.le}`,
          greater_than: `必须大于 ${context.gt}`,
          less_than: `必须小于 ${context.lt}`,
          int_parsing: "必须是整数", int_from_float: "必须是整数",
          float_parsing: "必须是数字", finite_number: "必须是有限数字",
          missing: "不能为空", list_type: "必须是数组",
        };
        return `${name}：${messages[value.type] || value.msg || "参数不合法"}`;
      }
      return describe(value.detail ?? value.message ?? value.error ?? value.msg) || JSON.stringify(value);
    }
    return describe(payload?.detail ?? payload?.message ?? payload?.error ?? payload) || fallback;
  }

  function annotationColor(label) {
    let seed = 2166136261;
    for (const character of String(label || "未命名").trim().normalize("NFC")) {
      seed = Math.imul(seed ^ character.codePointAt(0), 16777619) >>> 0;
    }
    seed = Math.imul(seed ^ (seed >>> 16), 2246822507) >>> 0;
    return `hsl(${seed % 360}, ${70 + ((seed >>> 8) % 15)}%, ${45 + ((seed >>> 16) % 10)}%)`;
  }

  const helpers = { augmentationNumber, augmentationBatches, formatRequestError, annotationColor };
  if (typeof module !== "undefined" && module.exports) module.exports = helpers;
  if (typeof window !== "undefined") window.VisionDatasetHelpers = helpers;
})();
