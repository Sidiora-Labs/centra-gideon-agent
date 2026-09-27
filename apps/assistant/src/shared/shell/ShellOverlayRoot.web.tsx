import * as React from "react";
import { createPortal } from "react-dom";
import { useShellTheme } from "./shellTheme";

const focusable = 'button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

export function ShellOverlayRoot({ children, onClose }: { children: React.ReactNode; onClose: () => void }) {
  const { mode, direction, palette } = useShellTheme();
  const [container, setContainer] = React.useState<HTMLDivElement | null>(null);
  const previousFocus = React.useRef<HTMLElement | null>(null);
  const close = React.useRef(onClose);
  close.current = onClose;

  React.useEffect(() => {
    previousFocus.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const element = document.createElement("div");
    element.setAttribute("data-gideon-assistant-overlay", "");
    element.tabIndex = -1;
    document.body.appendChild(element);
    setContainer(element);
    return () => {
      element.remove();
      previousFocus.current?.focus();
    };
  }, []);

  React.useEffect(() => {
    if (!container) return;
    container.setAttribute("data-theme", mode);
    container.setAttribute("dir", direction);
    container.style.backgroundColor = palette.shade;
  }, [container, mode, direction, palette]);

  React.useEffect(() => {
    if (!container) return;
    const first = container.querySelector<HTMLElement>(focusable);
    first?.focus();
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") { event.preventDefault(); close.current(); return; }
      if (event.key !== "Tab") return;
      const controls = Array.from(container.querySelectorAll<HTMLElement>(focusable));
      if (!controls.length) { event.preventDefault(); container.focus(); return; }
      const current = document.activeElement;
      const next = event.shiftKey ? controls[controls.length - 1] : controls[0];
      if (!container.contains(current) || (event.shiftKey && current === controls[0]) || (!event.shiftKey && current === controls[controls.length - 1])) {
        event.preventDefault(); next.focus();
      }
    };
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [container]);

  if (!container) return null;
  return createPortal(
    <div role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) close.current(); }}
      style={{ display: "flex", alignItems: "center", justifyContent: "center", width: "100%", height: "100%" }}>
      <div role="dialog" aria-modal="true" data-gideon-assistant-dialog=""
        style={{ display: "flex", maxWidth: "100%", maxHeight: "100%" }}>
        {children}
      </div>
    </div>, container,
  );
}
