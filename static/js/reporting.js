(() => {
  const key = "budget-client-errors";
  const readErrors = () => {
    try { return JSON.parse(localStorage.getItem(key) || "[]"); } catch { return []; }
  };
  const saveError = (error) => {
    const entries = readErrors();
    entries.push({ timestamp: new Date().toISOString(), ...error });
    localStorage.setItem(key, JSON.stringify(entries.slice(-20)));
  };
  window.addEventListener("error", (event) => saveError({ message: event.message, source: event.filename, line: event.lineno, column: event.colno, stack: event.error?.stack }));
  window.addEventListener("unhandledrejection", (event) => saveError({ message: String(event.reason), stack: event.reason?.stack }));

  const form = document.querySelector("#problem-form");
  if (!form) return;
  const page = document.querySelector("#report-page");
  const preview = document.querySelector("#report-preview");
  const hidden = document.querySelector("#report-json");
  const serverError = JSON.parse(page.dataset.serverError || "{}");
  const errorId = document.querySelector("#server-error-id").value;
  const assemble = () => {
    const report = {
      report_id: page.dataset.reportId,
      timestamp: page.dataset.timestamp,
      description: document.querySelector("#description").value,
      expected: document.querySelector("#expected").value,
      reproduction_steps: document.querySelector("#reproduction").value,
      server_error_id: errorId,
      diagnostics: {
        page: location.pathname,
        url: location.href,
        browser_user_agent: navigator.userAgent,
        viewport: { width: innerWidth, height: innerHeight, device_pixel_ratio: devicePixelRatio },
        language: navigator.language,
        platform: navigator.platform,
        app_version: document.querySelector('meta[name="app-version"]')?.content || "unknown",
        client_errors: readErrors(),
        associated_server_error: serverError,
      },
    };
    preview.textContent = JSON.stringify(report, null, 2);
    hidden.value = JSON.stringify(report);
  };
  form.addEventListener("input", assemble);
  form.addEventListener("submit", assemble);
  assemble();
})();
