import { useState } from "react";
import { SearchIcon } from "lucide-react";
import { afterEach, describe, expect, it } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { ToolCall } from "./tool-call";
import { ToolTimeline } from "./tool-timeline";
import { TerminalBlock } from "./terminal-block";
import { CodeDiff, type DiffLine } from "./code-diff";
import { ReviewableDiff, type DiffHunk } from "./reviewable-diff";
import { FileTree, type FileTreeNode } from "./file-tree";

afterEach(cleanup);

function ToolDisclosureJourney() {
  const [open, setOpen] = useState(false);
  const [running, setRunning] = useState(true);
  return (
    <>
      <ToolCall label="Searched" activeLabel="Searching" query="notes"
        request="List all notes" result="3 notes" running={running}
        open={open} onOpenChange={setOpen} />
      <button type="button" onClick={() => setRunning(false)}>Complete tool</button>
    </>
  );
}

describe("tool disclosure lifecycle", () => {
  it("retains controlled disclosure across a running-to-complete update", () => {
    render(<ToolDisclosureJourney />);
    expect(screen.getByText("Searching")).toHaveClass("shimmer");
    expect(screen.queryByText("3 notes")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /Searching/ }));
    expect(screen.getByText("List all notes")).toBeInTheDocument();
    expect(screen.getByText("3 notes")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Complete tool" }));
    expect(screen.getByText("Searched")).toBeInTheDocument();
    expect(screen.getByText("3 notes")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /Searched/ }));
    expect(screen.queryByText("3 notes")).not.toBeInTheDocument();
  });

  it("accepts an initially open controlled disclosure", () => {
    let nextOpen: boolean | undefined;
    render(<ToolCall label="Loaded" activeLabel="Loading" query="file"
      request="Read file" result="File loaded" running={false}
      open onOpenChange={(value) => { nextOpen = value; }} />);
    expect(screen.getByText("File loaded")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button"));
    expect(nextOpen).toBe(false);
    expect(screen.getByText("File loaded")).toBeInTheDocument();
  });
});

describe("tool timeline edge behavior", () => {
  it("renders no invented steps or statistics for empty inputs", () => {
    const { container } = render(
      <ToolTimeline steps={[]} visibleSteps={10} streaming={false}
        open onOpenChange={() => {}} restingLabel="No actions"
        activeLabel="Working" stats={[]} />,
    );
    expect(screen.getByText("No actions")).toBeInTheDocument();
    expect(container.querySelectorAll('[data-slot="collapsible-content"] .font-mono')).toHaveLength(0);
    expect(screen.queryByText("Working")).not.toHaveClass("shimmer");
  });

  it("limits fractional reveal to complete steps and puts activity on the last shown step", () => {
    render(<ToolTimeline
      steps={[
        { verb: "Scanning", chip: "a", icon: SearchIcon },
        { verb: "Indexing", chip: "b", icon: SearchIcon },
      ]}
      visibleSteps={1.9} streaming open onOpenChange={() => {}}
      restingLabel="Done" activeLabel="Working" stats={[]} />);
    expect(screen.getByText("Scanning")).toHaveClass("shimmer");
    expect(screen.queryByText("Indexing")).not.toBeInTheDocument();
    expect(screen.getByText("a")).toBeInTheDocument();
    expect(screen.queryByText("b")).not.toBeInTheDocument();
  });

  it("preserves a known zero change count in timeline statistics", () => {
    render(<ToolTimeline steps={[]} visibleSteps={0} streaming={false}
      open onOpenChange={() => {}} restingLabel="Done" activeLabel="Working"
      stats={[{ file: "unchanged.txt", added: 0, removed: 0 }]} />);
    expect(screen.getByText("unchanged.txt")).toBeInTheDocument();
    expect(screen.getByText("+0")).toBeInTheDocument();
    expect(screen.getByText("−0")).toBeInTheDocument();
  });
});

describe("terminal completion truth", () => {
  it("keeps a reported exit code hidden while a run is still active", () => {
    const { container } = render(
      <TerminalBlock command="task" lines={["pending"]} visibleCount={1}
        done={false} exitCode={9} variant="ink" />,
    );
    expect(screen.queryByText("exit 9")).not.toBeInTheDocument();
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
    expect(container.querySelector('[aria-hidden="true"]')).not.toBeNull();
  });

  it("treats null exit code as unknown rather than successful", () => {
    const { container } = render(
      <TerminalBlock command="task" lines={[]} visibleCount={0}
        done exitCode={null} />,
    );
    expect(screen.getByRole("status")).toHaveTextContent("Finished");
    expect(screen.queryByText("exit 0")).not.toBeInTheDocument();
    expect(container.querySelector(".text-emerald-500")).toBeNull();
  });

  it("renders all lines for an excessive count without duplicating the last line", () => {
    const { container } = render(
      <TerminalBlock command="task" lines={["one", "two", "three"]}
        visibleCount={100} done exitCode={0} />,
    );
    expect(screen.getByText("one")).toBeInTheDocument();
    expect(screen.getByText("two")).toBeInTheDocument();
    expect(screen.getByText("three")).toBeInTheDocument();
    expect(container.querySelectorAll('[data-slot="terminal-block"] .duration-300')).toHaveLength(3);
  });
});

describe("diff content integrity", () => {
  it("treats tool-produced source lines as text, never as HTML", () => {
    const lines: DiffLine[] = [
      { kind: "removed", text: "<script>window.bad=1</script>" },
      { kind: "added", text: "<div title=\"safe\">ready</div>" },
    ];
    const { container } = render(
      <CodeDiff filename="unsafe.html" additions={1} deletions={1} lines={lines} cycle={0} />,
    );
    expect(screen.getByText("<script>window.bad=1</script>")).toBeInTheDocument();
    expect(screen.getByText('<div title="safe">ready</div>')).toBeInTheDocument();
    expect(container.querySelector("script")).toBeNull();
    expect(container.querySelector('div[title="safe"]')).toBeNull();
  });

  it("updates filename, totals and lines when the caller receives a new diff", () => {
    const { rerender } = render(
      <CodeDiff filename="old.ts" additions={1} deletions={0}
        lines={[{ kind: "added", text: "old" }]} cycle={0} />,
    );
    expect(screen.getByText("old.ts")).toBeInTheDocument();
    expect(screen.getByText("old")).toBeInTheDocument();
    rerender(<CodeDiff filename="new.ts" additions={0} deletions={1}
      lines={[{ kind: "removed", text: "new" }]} cycle={1} />);
    expect(screen.getByText("new.ts")).toBeInTheDocument();
    expect(screen.getByText("−1")).toBeInTheDocument();
    expect(screen.getByText("+0")).toBeInTheDocument();
    expect(screen.getByText("new").parentElement).toHaveClass("whitespace-pre");
    expect(screen.queryByText("old")).not.toBeInTheDocument();
  });
});

describe("reviewable diff caller authority", () => {
  it("reports pre-reviewed decisions and allows apply only when none are pending", () => {
    const hunks: DiffHunk[] = [
      { id: "kept", range: "1", decision: "kept", lines: [] },
      { id: "discarded", range: "2", decision: "discarded", lines: [] },
    ];
    let applied = 0;
    const { container } = render(
      <ReviewableDiff filename="ready.ts" hunks={hunks}
        onApply={() => { applied += 1; }} />,
    );
    expect(screen.getByText("1 of 2 kept")).toBeInTheDocument();
    expect(screen.getByText("All reviewed")).toBeInTheDocument();
    expect(screen.getByText("kept")).toBeInTheDocument();
    expect(screen.getByText("discarded")).toBeInTheDocument();
    expect(container.querySelector(".opacity-40")).not.toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Apply 1" }));
    expect(applied).toBe(1);
  });

  it("keeps all source hunk text literal and horizontally scrollable", () => {
    const hunks: DiffHunk[] = [
      {
        id: "literal",
        range: "@@ 1 @@",
        decision: "pending",
        lines: [{ kind: "added", text: "<img src=x onerror=bad()>" }],
      },
    ];
    const { container } = render(
      <ReviewableDiff filename="view.html" hunks={hunks} />,
    );
    expect(screen.getByText("<img src=x onerror=bad()>")).toBeInTheDocument();
    expect(container.querySelector("img")).toBeNull();
    expect(container.querySelector(".overflow-x-auto")).not.toBeNull();
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });
});

describe("file tree caller data", () => {
  it("does not mistake folders for changed files", () => {
    const nodes: FileTreeNode[] = [
      { path: "a", name: "a", depth: 0, kind: "folder" },
      { path: "a/b", name: "b", depth: 1, kind: "folder" },
    ];
    render(<FileTree nodes={nodes} visibleCount={2}
      totalAdditions={0} totalDeletions={0} />);
    expect(screen.getByText("0 files changed")).toBeInTheDocument();
    expect(screen.getByText("a")).toBeInTheDocument();
    expect(screen.getByText("b")).toBeInTheDocument();
  });

  it("preserves caller order and shows only real per-file counts", () => {
    const nodes: FileTreeNode[] = [
      { path: "z.ts", name: "z.ts", depth: 0, kind: "file", deletions: 3 },
      { path: "a.ts", name: "a.ts", depth: 0, kind: "file", additions: 5 },
    ];
    const { container } = render(
      <FileTree nodes={nodes} visibleCount={2} totalAdditions={5} totalDeletions={3} />,
    );
    const rows = container.querySelectorAll('[data-slot="file-tree"] .truncate');
    expect(rows).toHaveLength(2);
    expect(rows[0]).toHaveTextContent("z.ts");
    expect(rows[1]).toHaveTextContent("a.ts");
    expect(screen.getByText("2 files changed")).toBeInTheDocument();
    expect(screen.getAllByText("−3")).toHaveLength(2);
    expect(screen.getAllByText("+5")).toHaveLength(2);
  });
});
