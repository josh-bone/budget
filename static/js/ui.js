document.querySelectorAll("[data-month-picker]").forEach((picker) => {
  picker.addEventListener("change", () => {
    window.location.assign(picker.selectedOptions[0].dataset.url);
  });
});

document.querySelectorAll("form[data-auto-submit] select").forEach((select) => {
  select.addEventListener("change", () => select.form?.requestSubmit());
});
