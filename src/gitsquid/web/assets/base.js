"use strict";

function el(tag, props = {}, children = []) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(props)) {
    if (value === undefined || value === null || value === false) continue;
    if (key === "class") node.className = value;
    else if (key === "text") node.textContent = value;
    // Through the CSSOM, never as a style attribute: the page's CSP forbids inline styles.
    else if (key === "style") for (const rule of String(value).split(";")) {
      const [property, setting] = rule.split(":");
      if (setting) node.style.setProperty(property.trim(), setting.trim());
    }
    else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
    else node.setAttribute(key, value === true ? "" : String(value));
  }
  return fill(node, children);
}

const $ = (id) => document.getElementById(id);

// Node.append() writes a null child out as the text "null"; a skipped branch must vanish.
const renderable = (child) => child !== null && child !== undefined && child !== false;

const fill = (node, ...children) => {
  node.append(...children.flat().filter(renderable).map((child) => (child.nodeType ? child : String(child))));
  return node;
};
const clear = (node) => { while (node.firstChild) node.firstChild.remove(); return node; };

function relativeTime(iso) {
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return iso.slice(0, 10);
  const seconds = Math.max(0, (Date.now() - then) / 1000);
  if (seconds < 60) return "just now";
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`;
  if (seconds < 2592000) return `${Math.floor(seconds / 86400)}d ago`;
  return new Date(then).toISOString().slice(0, 10);
}

const plural = (count, word) => `${count} ${word}${count === 1 ? "" : "s"}`;

function splitPath(path) {
  const cut = path.lastIndexOf("/");
  return cut === -1 ? ["", path] : [path.slice(0, cut + 1), path.slice(cut + 1)];
}

function copy(text, what) {
  navigator.clipboard.writeText(text).then(
    () => toast("ok", `${what} copied.`),
    () => toast("bad", "The clipboard refused the copy."),
  );
}

function ask({ title, hint = "", label, placeholder = "", value = "", submit = "OK", optional = false, extra = null }) {
  const modal = $("ask-modal");
  const field = $("ask-value");
  const form = $("ask-form");
  $("ask-title").textContent = title;
  $("ask-hint").textContent = hint;
  $("ask-hint").hidden = !hint;
  $("ask-label").textContent = label;
  $("ask-submit").textContent = submit;
  $("ask-error").hidden = true;
  field.placeholder = placeholder;
  field.value = value;
  $("ask-extra-field").hidden = !extra;
  if (extra) {
    $("ask-extra-label").textContent = extra.label;
    $("ask-extra").placeholder = extra.placeholder || "";
    $("ask-extra").value = "";
  }

  return new Promise((resolve) => {
    let settled = false;
    const finish = (result) => {
      if (settled) return;
      settled = true;
      form.removeEventListener("submit", onSubmit);
      modal.removeEventListener("close", onClose);
      resolve(result);
    };
    const onSubmit = (event) => {
      event.preventDefault();
      const primary = field.value.trim();
      if (!primary && !optional) {
        showFormError($("ask-error"), "This field is required.");
        return;
      }
      finish({ value: primary, extra: extra ? $("ask-extra").value.trim() : "" });
      modal.close();
    };
    const onClose = () => finish(null);
    form.addEventListener("submit", onSubmit);
    modal.addEventListener("close", onClose);
    modal.showModal();
    field.focus();
    field.select();
  });
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
  });
  let payload = {};
  try { payload = await response.json(); } catch { /* empty body */ }
  if (!response.ok) throw new Error(payload.error || `Request failed (${response.status}).`);
  return payload;
}

const post = (path, body) => api(path, { method: "POST", body: JSON.stringify(body || {}) });

function status(message, busy = false) {
  const target = clear($("status-text"));
  if (busy) target.append(el("span", { class: "spinner", "aria-hidden": "true" }), " ");
  target.append(message);
}

function toast(kind, message) {
  const node = el("div", { class: `toast ${kind}`, role: "status" }, [
    el("span", { class: "label", text: kind === "ok" ? "Success" : "Failure" }),
    el("span", { text: message }),
  ]);
  $("toasts").append(node);
  setTimeout(() => node.remove(), kind === "bad" ? 9000 : 5000);
}

async function withBusy(message, task) {
  if (state.busy) return undefined;
  state.busy = true;
  status(message, true);
  document.body.setAttribute("aria-busy", "true");
  try {
    return await task();
  } catch (error) {
    toast("bad", error.message);
    throw error;
  } finally {
    state.busy = false;
    status("Ready.");
    document.body.removeAttribute("aria-busy");
  }
}

const quiet = (promise) => promise.catch(() => {});

function remember(key, value) {
  try { localStorage.setItem(`gitsquid.${key}`, value); } catch { /* private mode */ }
}

function recall(key, fallback) {
  try { return localStorage.getItem(`gitsquid.${key}`) ?? fallback; } catch { return fallback; }
}
