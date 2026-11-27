document.querySelectorAll("[data-month-picker]").forEach((picker) => {
  picker.addEventListener("change", () => {
    window.location.assign(`/month/${encodeURIComponent(picker.value)}`);
  });
});

document.querySelectorAll("form[data-auto-submit] select").forEach((select) => {
  select.addEventListener("change", () => select.form?.requestSubmit());
});
