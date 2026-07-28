const healthButton = document.querySelector("[data-health-check]");
const healthStatus = document.querySelector("[data-health-status]");

if (healthButton && healthStatus) {
  healthButton.addEventListener("click", async () => {
    healthButton.disabled = true;
    healthStatus.textContent = "检查中…";

    try {
      const response = await fetch("/api/v1/health");
      const payload = await response.json();
      healthStatus.textContent = payload.status === "READY" ? "运行正常" : payload.status;
      healthStatus.className = "fs-4 fw-semibold mt-2 text-success";
    } catch {
      healthStatus.textContent = "连接失败";
      healthStatus.className = "fs-4 fw-semibold mt-2 text-danger";
    } finally {
      healthButton.disabled = false;
    }
  });
}

