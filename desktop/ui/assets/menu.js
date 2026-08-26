"use strict";

// Items are {label, run, hint, danger, disabled}, a {header}, or "-" for a separator.
const Menu = (() => {
  let open = null;

  function close() {
    if (!open) return;
    const { node, restore } = open;
    open = null;
    node.remove();
    document.removeEventListener("pointerdown", onPointerDown, true);
    document.removeEventListener("keydown", onKeyDown, true);
    window.removeEventListener("resize", close);
    window.removeEventListener("blur", close);
    if (restore && restore.isConnected) restore.focus();
  }

  function entries() {
    return [...open.node.querySelectorAll('[role="menuitem"]:not([disabled])')];
  }

  function step(delta) {
    const items = entries();
    if (!items.length) return;
    const at = items.indexOf(document.activeElement);
    items[(at + delta + items.length) % items.length].focus();
  }

  function onPointerDown(event) {
    if (open && !open.node.contains(event.target)) close();
  }

  function onKeyDown(event) {
    if (!open) return;
    const keys = {
      Escape: close,
      ArrowDown: () => step(1),
      ArrowUp: () => step(-1),
      Home: () => entries()[0]?.focus(),
      End: () => entries().at(-1)?.focus(),
      Tab: close,
    };
    const action = keys[event.key];
    if (action) {
      if (event.key !== "Tab") event.preventDefault();
      action();
    }
  }

  function build(items, run) {
    const node = document.createElement("div");
    node.className = "context-menu";
    node.setAttribute("role", "menu");
    for (const item of items) {
      if (!item) continue;
      if (item === "-") {
        node.append(Object.assign(document.createElement("hr"), { role: "separator" }));
        continue;
      }
      if (item.header) {
        const header = document.createElement("div");
        header.className = `menu-header${item.plain ? " plain" : ""}`;
        header.textContent = item.header;
        node.append(header);
        continue;
      }
      const button = document.createElement("button");
      button.type = "button";
      button.className = `menu-item${item.danger ? " danger" : ""}${item.className ? ` ${item.className}` : ""}`;
      button.setAttribute("role", "menuitem");
      button.disabled = Boolean(item.disabled);
      if (item.title) button.title = item.title;
      const label = document.createElement("span");
      label.textContent = item.label;
      button.append(label);
      if (item.hint) {
        const hint = document.createElement("span");
        hint.className = "menu-hint";
        hint.textContent = item.hint;
        button.append(hint);
      }
      button.addEventListener("click", () => { close(); run(item); });
      node.append(button);
    }
    return node;
  }

  function place(node, x, y) {
    const { width, height } = node.getBoundingClientRect();
    const left = x + width + 8 > window.innerWidth ? Math.max(8, x - width) : x;
    const top = y + height + 8 > window.innerHeight ? Math.max(8, y - height) : y;
    node.style.left = `${left}px`;
    node.style.top = `${top}px`;
  }

  // `at` is a pointer event or an element: the right click and the menu key land alike.
  function show(at, items) {
    close();
    const usable = items.filter(Boolean);
    if (!usable.length) return;
    const node = build(usable, (item) => item.run());
    document.body.append(node);
    open = { node, restore: document.activeElement };

    const point = at && typeof at.clientX === "number" && (at.clientX || at.clientY)
      ? { x: at.clientX, y: at.clientY }
      : (() => {
          const box = (at.currentTarget || at.target || at).getBoundingClientRect();
          return { x: box.left + 12, y: box.bottom - 4 };
        })();
    place(node, point.x, point.y);
    entries()[0]?.focus();

    document.addEventListener("pointerdown", onPointerDown, true);
    document.addEventListener("keydown", onKeyDown, true);
    window.addEventListener("resize", close);
    window.addEventListener("blur", close);
  }

  function attach(node, items) {
    node.addEventListener("contextmenu", (event) => {
      event.preventDefault();
      show(event, typeof items === "function" ? items() : items);
    });
    node.addEventListener("keydown", (event) => {
      if (event.key !== "ContextMenu" && !(event.key === "F10" && event.shiftKey)) return;
      event.preventDefault();
      show(node, typeof items === "function" ? items() : items);
    });
    return node;
  }

  return { show, attach, close };
})();
