"use strict";
window.ModelHealth = (() => {
  const pending = new Map();
  function show(host, id) {
    host.replaceChildren();
    if (!id) return;
    const title = document.createElement("strong"), text = document.createElement("p");
    const details = document.createElement("p"), retry = document.createElement("button");
    host.dataset.state = "checking";
    host.className = "model-health"; host.setAttribute("role", "status");
    title.textContent = id + " · Testing inference…";
    text.textContent = "One short request using saved settings (128 output tokens maximum, 30-second timeout).";
    retry.type = "button"; retry.textContent = "Test again"; retry.disabled = true;
    retry.onclick = () => show(host, id);
    host.append(title, text, details, retry);
    host.setAttribute("aria-busy", "true");
    if (!pending.has(id)) {
      pending.set(id, fetch("/api/models/" + encodeURIComponent(id) + "/test", {method:"POST", signal:AbortSignal.timeout(35000)})
        .then(async response => {
          if (!response.ok) throw new Error("Cannot read or test the saved model configuration.");
          return response.json();
        }).finally(() => pending.delete(id)));
    }
    pending.get(id).then(result => {
      if (!host.contains(title)) return;
      title.textContent = id + (result.ok ? " · Inference available" : " · " + result.code);
      host.dataset.state = result.ok ? "ready" : "error";
      text.textContent = result.message + " " + result.hint;
      details.textContent = result.protocol + " · " + result.latency_ms + " ms · " +
        new Date(result.checked_at).toLocaleTimeString() + " · Short connection check; not an evaluation.";
    }).catch(error => {
      if (!host.contains(title)) return;
      host.dataset.state = "error"; title.textContent = id + " · Test unavailable";
      text.textContent = error.message; details.textContent = "Check the application server connection and retry.";
    }).finally(() => {
      if (host.contains(title)) { retry.disabled = false; host.setAttribute("aria-busy", "false"); }
    });
  }
  function dashboard() {
    let host = document.getElementById("model-health");
    if (!host) {
      host = document.createElement("div"); host.id = "model-health";
      document.querySelector(".launch-row").after(host);
    }
    show(host, document.getElementById("model-select").value);
  }
  document.addEventListener("change", event => {
    const input = event.target;
    if (input.id === "model-select") dashboard();
    if (input.matches('input[name="model"], input[name="probe-model"]')) {
      const parent = input.closest(".choices");
      let host = [...parent.parentElement.children].find(n => n.dataset.modelId === input.value);
      if (!host) {
        host = document.createElement("div"); host.dataset.modelId = input.value;
        parent.after(host);
      }
      if (input.checked) show(host, input.value); else host.remove();
    }
  });
  return {show, dashboard};
})();
