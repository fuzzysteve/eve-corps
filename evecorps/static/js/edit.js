// Advert editor: Write/Preview tabs and in-game description import.
// Kept as an external file because the CSP forbids inline scripts.
(function () {
  const form = document.querySelector("form[method=post]");
  const body = document.getElementById("body");
  const preview = document.getElementById("preview");
  const tabWrite = document.getElementById("tab-write");
  const tabPreview = document.getElementById("tab-preview");
  const importBtn = document.getElementById("import-ingame");
  const status = document.getElementById("import-status");
  if (!form || !body) return;
  const csrf = form.querySelector("input[name=csrf_token]").value;

  function showWrite() {
    tabWrite.classList.add("active");
    tabPreview.classList.remove("active");
    preview.classList.add("d-none");
    body.classList.remove("d-none");
  }

  async function showPreview() {
    tabPreview.classList.add("active");
    tabWrite.classList.remove("active");
    body.classList.add("d-none");
    preview.classList.remove("d-none");
    preview.textContent = "Loading preview…";
    try {
      const resp = await fetch(tabPreview.dataset.url, {
        method: "POST",
        body: new URLSearchParams({ csrf_token: csrf, body: body.value }),
        credentials: "same-origin",
      });
      if (resp.status === 429) {
        preview.textContent = await resp.text();
        return;
      }
      if (!resp.ok) throw new Error(resp.status);
      // Server output is rendered and sanitized (nh3) before it gets here.
      preview.innerHTML = await resp.text();
    } catch (e) {
      preview.textContent = "Preview failed. Your login may have expired; reload the page.";
    }
  }

  async function importIngame() {
    if (body.value.trim() && !confirm("Replace the current description with your in-game corporation description?")) return;
    importBtn.disabled = true;
    status.textContent = "Fetching from EVE…";
    try {
      const resp = await fetch(importBtn.dataset.url, { credentials: "same-origin" });
      const data = await resp.json().catch(() => ({}));
      if (!resp.ok) throw new Error(data.error || "Import failed, reload the page and try again.");
      if (!data.markdown) {
        status.textContent = "Your corporation has no in-game description.";
        return;
      }
      body.value = data.markdown.slice(0, Number(body.maxLength) || 10000);
      showWrite();
      status.textContent = "Imported. Colours and sizes from the game aren't kept, and in-game links became plain text. Check it over before saving.";
    } catch (e) {
      status.textContent = e.message;
    } finally {
      importBtn.disabled = false;
    }
  }

  const pasteToggle = document.getElementById("paste-toggle");
  const pastePanel = document.getElementById("paste-panel");
  const pasteSrc = document.getElementById("paste-src");
  const pasteConvert = document.getElementById("paste-convert");

  function togglePaste(open) {
    pastePanel.classList.toggle("d-none", !open);
    pasteToggle.setAttribute("aria-expanded", String(open));
    if (open) pasteSrc.focus();
  }

  async function convertPaste() {
    if (!pasteSrc.value.trim()) {
      status.textContent = "Paste your advert text first.";
      return;
    }
    if (body.value.trim() && !confirm("Replace the current description with the pasted advert?")) return;
    pasteConvert.disabled = true;
    status.textContent = "Converting…";
    try {
      const resp = await fetch(pasteConvert.dataset.url, {
        method: "POST",
        body: new URLSearchParams({ csrf_token: csrf, text: pasteSrc.value }),
        credentials: "same-origin",
      });
      const data = await resp.json().catch(() => ({}));
      if (!resp.ok) throw new Error(data.error || "Conversion failed, reload the page and try again.");
      body.value = data.markdown.slice(0, Number(body.maxLength) || 10000);
      pasteSrc.value = "";
      togglePaste(false);
      showWrite();
      status.textContent = data.format === "eve"
        ? "Converted from in-game formatting. Colours and sizes aren't kept, and in-game links became plain text. Check it over before saving."
        : "Pasted as plain text. Tags like timezone and activities don't come across, so tick those below.";
    } catch (e) {
      status.textContent = e.message;
    } finally {
      pasteConvert.disabled = false;
    }
  }

  pasteToggle.addEventListener("click", () => togglePaste(pastePanel.classList.contains("d-none")));
  document.getElementById("paste-cancel").addEventListener("click", () => togglePaste(false));
  pasteConvert.addEventListener("click", convertPaste);
  tabWrite.addEventListener("click", showWrite);
  tabPreview.addEventListener("click", showPreview);
  importBtn.addEventListener("click", importIngame);
})();
