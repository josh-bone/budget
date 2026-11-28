interface TransactionRow {
  category_id: number | null;
  category: string | null;
  manual: boolean;
}

interface SpendCategory {
  name: string;
  amount: number;
  percent: number;
  color: string;
}

interface SpendResponse {
  total: number;
  breakdown: SpendCategory[];
}

interface TrendPoint {
  date: string;
  amount: number;
}

const money = (value: number): string =>
  value.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });

function drawPie(canvas: HTMLCanvasElement, categories: SpendCategory[]): void {
  const context = canvas.getContext("2d");
  if (!context) return;
  const bounds = canvas.getBoundingClientRect();
  const size = Math.min(bounds.width, bounds.height);
  const scale = window.devicePixelRatio || 1;
  canvas.width = size * scale;
  canvas.height = size * scale;
  context.setTransform(scale, 0, 0, scale, 0, 0);
  context.clearRect(0, 0, size, size);
  const total = categories.reduce((sum, item) => sum + item.amount, 0);
  let angle = -Math.PI / 2;
  for (const item of categories) {
    const next = angle + (total ? item.amount / total : 0) * Math.PI * 2;
    context.beginPath();
    context.moveTo(size / 2, size / 2);
    context.arc(size / 2, size / 2, size / 2, angle, next);
    context.closePath();
    context.fillStyle = item.color;
    context.fill();
    angle = next;
  }
  if (!categories.length) {
    context.beginPath();
    context.arc(size / 2, size / 2, size / 2, 0, Math.PI * 2);
    context.fillStyle = "#e5e7eb";
    context.fill();
  }

  // Canvas slices have no native hover target, so map the pointer back to the
  // wedge and show its category in a floating tooltip.
  let tooltip = canvas.parentElement?.querySelector<HTMLElement>(".pie-tooltip");
  if (!tooltip && canvas.parentElement) {
    tooltip = document.createElement("div");
    tooltip.className = "pie-tooltip";
    tooltip.setAttribute("role", "tooltip");
    tooltip.hidden = true;
    canvas.parentElement.append(tooltip);
  }
  canvas.onmousemove = (event: MouseEvent) => {
    if (!tooltip || !categories.length || !total) return;
    const rect = canvas.getBoundingClientRect();
    const x = event.clientX - rect.left - rect.width / 2;
    const y = event.clientY - rect.top - rect.height / 2;
    if (Math.hypot(x, y) > size / 2) {
      tooltip.hidden = true;
      return;
    }
    let pointerAngle = Math.atan2(y, x) + Math.PI / 2;
    if (pointerAngle < 0) pointerAngle += Math.PI * 2;
    let sliceEnd = 0;
    const category = categories.find((item) => {
      sliceEnd += (item.amount / total) * Math.PI * 2;
      return pointerAngle < sliceEnd;
    });
    if (!category) return;
    tooltip.textContent = category.name;
    tooltip.hidden = false;
    tooltip.style.left = `${event.clientX + 12}px`;
    tooltip.style.top = `${event.clientY + 12}px`;
  };
  canvas.onmouseleave = () => {
    if (tooltip) tooltip.hidden = true;
  };
}

async function refreshSpend(section: HTMLElement): Promise<void> {
  const form = document.querySelector<HTMLFormElement>("form.filters");
  const canvas = section.querySelector<HTMLCanvasElement>("canvas");
  if (!form || !canvas) return;
  const url = new URL(section.dataset.spendUrl ?? "", window.location.href);
  const params = new URLSearchParams();
  new FormData(form).forEach((value, key) => {
    if (typeof value === "string") params.append(key, value);
  });
  const leafView = document.querySelector<HTMLInputElement>('form.filters input[name="leaf"]');
  params.set("leaf", leafView?.checked ? "1" : "0");
  url.search = params.toString();
  const response = await fetch(url);
  if (!response.ok) throw new Error("Could not refresh spending chart");
  const data = (await response.json()) as SpendResponse;
  drawPie(canvas, data.breakdown);
  section.querySelector(".spend-total")!.textContent = `Total spend: $${money(data.total)}`;
  const legend = section.querySelector<HTMLUListElement>(".legend");
  const empty = section.querySelector<HTMLParagraphElement>(".muted");
  if (data.breakdown.length) {
    const list = legend ?? document.createElement("ul");
    list.className = "legend";
    list.replaceChildren(
      ...data.breakdown.map((item) => {
        const entry = document.createElement("li");
        const swatch = document.createElement("span");
        swatch.className = "swatch";
        swatch.style.backgroundColor = item.color;
        const label = document.createElement("span");
        label.textContent = `${item.name}: $${money(item.amount)} (${item.percent.toFixed(1)}%)`;
        entry.append(swatch, label);
        return entry;
      }),
    );
    if (!legend) empty?.replaceWith(list);
  } else if (!empty) {
    const message = document.createElement("p");
    message.className = "muted";
    message.textContent = "No spending found for this period.";
    legend?.replaceWith(message);
  }
}

function initCategoryDropdown(): void {
  const dropdown = document.getElementById("catDropdown");
  const summary = document.getElementById("catSummary");
  const selectAll = document.getElementById("catSelectAll") as HTMLInputElement | null;
  if (!dropdown || !summary || !selectAll) return;
  const checkboxes = Array.from(dropdown.querySelectorAll<HTMLInputElement>(".cat-cb"));
  const sync = (): void => {
    const checked = checkboxes.filter((checkbox) => checkbox.checked);
    summary.textContent =
      checked.length === 0
        ? "All categories"
        : checked.length === checkboxes.length
          ? `All (${checked.length})`
          : checked.length === 1
            ? checked[0]!.parentElement!.textContent!.trim()
            : `${checked.length} selected`;
    selectAll.checked = checkboxes.length > 0 && checked.length === checkboxes.length;
    selectAll.indeterminate = checked.length > 0 && checked.length < checkboxes.length;
  };
  selectAll.addEventListener("change", () => {
    checkboxes.forEach((checkbox) => {
      checkbox.checked = selectAll.checked;
    });
    sync();
  });
  checkboxes.forEach((checkbox) => checkbox.addEventListener("change", sync));
  document.addEventListener("click", (event) => {
    if (!dropdown.contains(event.target as Node)) dropdown.removeAttribute("open");
  });
  sync();
}

function initDateFilters(): void {
  const form = document.querySelector<HTMLFormElement>("form.filters");
  const month = form?.querySelector<HTMLSelectElement>('select[name="month"]');
  const start = form?.querySelector<HTMLInputElement>('input[name="start_date"]');
  const end = form?.querySelector<HTMLInputElement>('input[name="end_date"]');
  if (!month || !start || !end) return;
  month.addEventListener("change", () => {
    if (month.value) {
      start.value = "";
      end.value = "";
    }
  });
  const clearMonth = (): void => {
    if (start.value || end.value) month.value = "";
  };
  start.addEventListener("change", clearMonth);
  end.addEventListener("change", clearMonth);
}

function initCategoryEditing(): void {
  document.querySelectorAll<HTMLFormElement>(".category-form, .reset-form").forEach((form) => {
    form
      .querySelector<HTMLSelectElement>('select[name="category_id"]')
      ?.addEventListener("change", () => form.requestSubmit());
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      const row = form.closest<HTMLTableRowElement>("tr");
      if (!row) return;
      const select = form.querySelector<HTMLSelectElement>('select[name="category_id"]');
      const submittedValue = select?.value ?? "";
      try {
        const response = await fetch(form.action, {
          method: "POST",
          headers: { "X-Requested-With": "fetch" },
          body: new FormData(form),
        });
        const result = (await response.json()) as TransactionRow | { error: string };
        if (!response.ok || "error" in result)
          throw new Error("error" in result ? result.error : "Update failed");
        row.querySelector(".manual")?.remove();
        if (result.manual) {
          const badge = document.createElement("span");
          badge.className = "manual";
          badge.title = "Manually categorized";
          badge.textContent = "Manual";
          row.querySelector(".category-form")?.append(" ", badge);
        }
        const reset = row.querySelector<HTMLFormElement>(".reset-form");
        if (reset) reset.style.display = result.manual ? "inline" : "none";
        const activeCategory = new URLSearchParams(window.location.search).get("category_id");
        if (
          activeCategory &&
          (activeCategory === "uncategorized"
            ? result.category_id !== null
            : String(result.category_id) !== activeCategory)
        )
          row.remove();
        if (!document.querySelector("tbody tr")) {
          const body = document.querySelector("tbody");
          if (body)
            body.innerHTML = '<tr><td colspan="6">No transactions match these filters.</td></tr>';
        }
        const count = document.querySelector("body > p.muted");
        if (count && row.isConnected === false) {
          const current = Number(count.textContent?.match(/\d+/)?.[0] ?? 0);
          count.textContent = `${Math.max(0, current - 1)} transaction${current === 2 ? "" : "s"}`;
        }
        const spend = document.querySelector<HTMLElement>(".spend");
        if (spend) await refreshSpend(spend);
      } catch (error) {
        if (select) select.value = submittedValue;
        window.alert(error instanceof Error ? error.message : "Could not update category.");
      }
    });
  });
}

function initTrend(): void {
  const canvas = document.getElementById("net-worth-chart") as HTMLCanvasElement | null;
  const url = canvas?.dataset.trendUrl;
  if (!canvas || !url) return;
  fetch(url)
    .then((response) => response.json() as Promise<TrendPoint[]>)
    .then((data) => {
      const context = canvas.getContext("2d");
      if (!context || !data.length) return;
      const draw = (): void => {
        const bounds = canvas.getBoundingClientRect();
        const scale = window.devicePixelRatio || 1;
        canvas.width = bounds.width * scale;
        canvas.height = bounds.height * scale;
        context.setTransform(scale, 0, 0, scale, 0, 0);
        const width = bounds.width,
          height = bounds.height,
          pad = { left: 60, right: 12, top: 14, bottom: 34 };
        const values = data.map((point) => point.amount);
        const min = Math.min(...values),
          max = Math.max(...values),
          span = max - min || Math.max(Math.abs(max) * 0.1, 1);
        const x = (index: number): number =>
          pad.left +
          (data.length === 1
            ? (width - pad.left - pad.right) / 2
            : (index * (width - pad.left - pad.right)) / (data.length - 1));
        const y = (value: number): number =>
          pad.top + ((max - value + span * 0.08) * (height - pad.top - pad.bottom)) / (span * 1.16);
        context.clearRect(0, 0, width, height);
        context.font = "10px DM Mono, monospace";
        context.fillStyle = "#6b6760";
        context.strokeStyle = "#d4cfc6";
        for (let index = 0; index < 4; index++) {
          const value = min + (span * index) / 3,
            yy = y(value);
          context.beginPath();
          context.moveTo(pad.left, yy);
          context.lineTo(width - pad.right, yy);
          context.stroke();
          context.textAlign = "right";
          context.fillText(`$${Math.round(value).toLocaleString()}`, pad.left - 8, yy + 3);
        }
        context.beginPath();
        data.forEach((point, index) =>
          index
            ? context.lineTo(x(index), y(point.amount))
            : context.moveTo(x(index), y(point.amount)),
        );
        context.strokeStyle = "#2c5f8a";
        context.lineWidth = 2;
        context.stroke();
        data.forEach((point, index) => {
          context.beginPath();
          context.arc(x(index), y(point.amount), 3, 0, Math.PI * 2);
          context.fillStyle = "#2c5f8a";
          context.fill();
          if (index === 0 || index === data.length - 1 || data.length < 7) {
            context.fillStyle = "#6b6760";
            context.textAlign =
              index === 0 ? "left" : index === data.length - 1 ? "right" : "center";
            context.fillText(point.date, x(index), height - 10);
          }
        });
      };
      draw();
      window.addEventListener("resize", draw);
    });
}

initCategoryDropdown();
initDateFilters();
initCategoryEditing();
initTrend();
const pie = document.querySelector<HTMLCanvasElement>(".spend canvas");
if (pie) {
  const entries = Array.from(document.querySelectorAll<HTMLElement>(".legend li"));
  const initial = entries.map((entry) => ({
    name: entry.textContent?.split(":")[0] ?? "",
    amount: Number(entry.textContent?.match(/\$([\d,]+(?:\.\d+)?)/)?.[1]?.replaceAll(",", "") ?? 0),
    percent: 0,
    color: getComputedStyle(entry.querySelector(".swatch")!).backgroundColor,
  }));
  drawPie(pie, initial);
  window.addEventListener("resize", () => drawPie(pie, initial));
}
