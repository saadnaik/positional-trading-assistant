const button = document.querySelector("#run-analysis");
const panel = document.querySelector("#run-status");

function renderStatus(data) {
  document.querySelector("#status-value").textContent = data.status;
  document.querySelector("#session-value").textContent = data.session_status;
  const progress = document.querySelector("#progress-line");
  progress.hidden = data.status !== "RUNNING";
  document.querySelector("#phase-value").textContent = (data.phase || "").replaceAll("_", " ");
  document.querySelector("#current-symbol").textContent = data.current_symbol || "";
  document.querySelector("#progress-value").textContent = data.total == null ? `${data.processed}` : `${data.processed} / ${data.total}`;
  const error = document.querySelector("#run-error");
  error.hidden = !data.error;
  error.textContent = data.error || "";
  button.disabled = data.status === "RUNNING";
}

async function poll() {
  const response = await fetch("/api/runs/current", {headers: {"Accept": "application/json"}});
  const data = await response.json();
  renderStatus(data);
  if (data.status === "RUNNING") window.setTimeout(poll, 2500);
  else if (panel.dataset.status === "RUNNING" || data.status === "COMPLETED") window.location.reload();
}

if (button) button.addEventListener("click", async () => {
  button.disabled = true;
  const response = await fetch("/api/runs", {method: "POST", headers: {"Accept": "application/json"}});
  const data = await response.json();
  if (!response.ok) {
    button.disabled = false;
    document.querySelector("#run-error").textContent = data.detail || "Could not start analysis.";
    document.querySelector("#run-error").hidden = false;
    return;
  }
  panel.dataset.status = "RUNNING";
  renderStatus(data);
  window.setTimeout(poll, 1000);
});

if (panel && panel.dataset.status === "RUNNING") window.setTimeout(poll, 1000);

document.querySelectorAll(".clickable-row").forEach(row => row.addEventListener("click", event => {
  if (!event.target.closest("a")) window.location.assign(row.dataset.href);
}));
