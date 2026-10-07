"use client";

import { renderMermaidSVG } from "beautiful-mermaid";
import { Maximize2, Minus, Plus, RotateCcw } from "lucide-react";
import {
  type FC,
  type ReactNode,
  memo,
  useCallback,
  useMemo,
  useRef,
  useState,
} from "react";
import { Modal } from "../../../ui/Modal";
import { IconButton } from "../../../ui/IconButton";
import { cn } from "../lib/utils";

export type MermaidDiagramProps = {
  code: string;
  className?: string;
  /** Renders a skeleton instead of the diagram while `true`. */
  streaming?: boolean;
};

const MIN_SCALE = 0.5;
const MAX_SCALE = 4;

type MermaidZoomProps = {
  svg: string;
  children: ReactNode;
};

function MermaidZoom({ svg, children }: MermaidZoomProps) {
  const [isOpen, setIsOpen] = useState(false);
  const [transform, setTransform] = useState({ x: 0, y: 0, scale: 1 });
  const triggerRef = useRef<HTMLButtonElement>(null);
  const viewportRef = useRef<HTMLDivElement>(null);
  const drag = useRef<{
    startX: number;
    startY: number;
    originX: number;
    originY: number;
  } | null>(null);
  const transformRef = useRef(transform);
  transformRef.current = transform;

  const zoomSvg = useMemo(
    () =>
      svg
        .replace(/id="([^"]+)"/g, 'id="$1-zoom"')
        .replace(/url\(#([^)]+)\)/g, "url(#$1-zoom)")
        .replace(/(href|xlink:href)="#([^"]+)"/g, '$1="#$2-zoom"'),
    [svg],
  );

  const handleClose = useCallback(() => {
    setIsOpen(false);
    setTransform({ x: 0, y: 0, scale: 1 });
  }, []);

  const zoomBy = useCallback((factor: number, cx?: number, cy?: number) => {
    setTransform((t) => {
      const scale = Math.min(MAX_SCALE, Math.max(MIN_SCALE, t.scale * factor));
      const ratio = scale / t.scale;
      if (cx === undefined || cy === undefined) {
        const viewport = viewportRef.current!;
        cx = viewport.clientWidth / 2;
        cy = viewport.clientHeight / 2;
      }
      return {
        scale,
        x: cx - (cx - t.x) * ratio,
        y: cy - (cy - t.y) * ratio,
      };
    });
  }, []);

  const onWheel = useCallback(
    (e: React.WheelEvent) => {
      const rect = e.currentTarget.getBoundingClientRect();
      zoomBy(
        Math.exp(-e.deltaY * 0.0015),
        e.clientX - rect.left,
        e.clientY - rect.top,
      );
    },
    [zoomBy],
  );

  const onPointerDown = useCallback((e: React.PointerEvent) => {
    e.currentTarget.setPointerCapture?.(e.pointerId);
    const t = transformRef.current;
    drag.current = {
      startX: e.clientX,
      startY: e.clientY,
      originX: t.x,
      originY: t.y,
    };
  }, []);

  const onPointerMove = useCallback((e: React.PointerEvent) => {
    const d = drag.current;
    if (!d) return;
    setTransform((t) => ({
      ...t,
      x: d.originX + e.clientX - d.startX,
      y: d.originY + e.clientY - d.startY,
    }));
  }, []);

  const onPointerUp = useCallback(() => {
    drag.current = null;
  }, []);

  return (
    <div
      data-slot="mermaid-zoom-wrap"
      className="aui-mermaid-zoom-wrap group/mermaid relative"
    >
      {children}
      <button
        ref={triggerRef}
        type="button"
        data-slot="mermaid-zoom-trigger"
        aria-label="Expand diagram"
        onClick={() => setIsOpen(true)}
        className="aui-mermaid-zoom-trigger text-muted-foreground hover:text-foreground hover:border-muted-foreground/70 border-border bg-background absolute top-2 right-2 cursor-pointer rounded-md border p-1.5 opacity-0 transition group-hover/mermaid:opacity-100 focus-visible:opacity-100"
      >
        <Maximize2 className="size-3.5" />
      </button>
      {isOpen && <Modal title="Diagram" presentation="fullscreen" chrome="media" restoreFocus={triggerRef}
        dismissOnBackdrop={false} lockBodyScroll onClose={handleClose} mediaControls={<>
          <IconButton icon={Plus} label="Zoom in" size={28} iconSize={16} onClick={() => zoomBy(1.25)} />
          <IconButton icon={Minus} label="Zoom out" size={28} iconSize={16} onClick={() => zoomBy(0.8)} />
          <IconButton icon={RotateCcw} label="Reset zoom" size={28} iconSize={16} onClick={() => setTransform({ x: 0, y: 0, scale: 1 })} />
        </>}>
            <div
              ref={viewportRef}
              className="aui-mermaid-zoom-viewport bg-background h-full w-full cursor-grab touch-none overflow-hidden active:cursor-grabbing"
              onWheel={onWheel}
              onPointerDown={onPointerDown}
              onPointerMove={onPointerMove}
              onPointerUp={onPointerUp}
              onPointerCancel={onPointerUp}
            >
              <div
                data-slot="mermaid-zoom-content"
                className="aui-mermaid-zoom-content flex h-full w-full items-center justify-center [&_svg]:max-h-[80vh] [&_svg]:max-w-[90vw]"
                style={{
                  transform: `translate(${transform.x}px, ${transform.y}px) scale(${transform.scale})`,
                  transformOrigin: "0 0",
                }}
                dangerouslySetInnerHTML={{ __html: zoomSvg }}
              />
            </div>
      </Modal>}
    </div>
  );
}

const MermaidDiagramImpl: FC<MermaidDiagramProps> = ({
  code,
  className,
  streaming = false,
}) => {
  const result = useMemo(() => {
    if (streaming) return null;
    try {
      return {
        svg: renderMermaidSVG(code, {
          bg: "var(--background)",
          fg: "var(--foreground)",
          muted: "var(--muted-foreground)",
          border: "var(--border)",
          accent: "var(--foreground)",
          transparent: true,
        }),
        error: null,
      };
    } catch {
      return { svg: null, error: true };
    }
  }, [streaming, code]);

  if (!result) {
    return (
      <div
        data-slot="mermaid-skeleton"
        aria-label="Rendering diagram"
        className={cn(
          "aui-mermaid-skeleton bg-muted flex h-32 animate-pulse items-center justify-center gap-3 rounded-b-lg p-4",
          className,
        )}
      >
        <div className="bg-muted-foreground/20 h-8 w-20 rounded-md" />
        <div className="bg-muted-foreground/20 h-px w-10" />
        <div className="bg-muted-foreground/20 h-8 w-20 rounded-md" />
        <div className="bg-muted-foreground/20 h-px w-10" />
        <div className="bg-muted-foreground/20 h-8 w-20 rounded-md" />
      </div>
    );
  }

  if (result.error || result.svg === null) {
    return (
      <div
        data-slot="mermaid-fallback"
        className={cn(
          "aui-mermaid-fallback bg-muted/75 rounded-b-lg",
          className,
        )}
      >
        <pre tabIndex={0} aria-label="Diagram source" className="overflow-x-auto p-4 text-sm">{code.trim()}</pre>
        <p className="text-muted-foreground border-border border-t px-4 py-1.5 text-xs">
          diagram could not be rendered
        </p>
      </div>
    );
  }

  return (
    <MermaidZoom svg={result.svg}>
      <div
        data-slot="mermaid-diagram"
        className={cn(
          "aui-mermaid-diagram bg-muted overflow-x-auto rounded-b-lg p-2 [&_svg]:mx-auto [&_svg]:h-auto [&_svg]:max-w-full",
          className,
        )}
        dangerouslySetInnerHTML={{ __html: result.svg }}
      />
    </MermaidZoom>
  );
};

const MermaidDiagram = memo(
  MermaidDiagramImpl,
) as unknown as FC<MermaidDiagramProps> & {
  Zoom: typeof MermaidZoom;
};

MermaidDiagram.displayName = "MermaidDiagram";
MermaidDiagram.Zoom = MermaidZoom;

export { MermaidDiagram, MermaidZoom };
