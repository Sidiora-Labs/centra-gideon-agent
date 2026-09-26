import { useState } from "react";
import { FileIcon, SearchIcon } from "lucide-react";
import { afterEach, describe, expect, it } from "vitest";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { ToolCall } from "./tool-call";
import { ToolTimeline, type TimelineStep } from "./tool-timeline";
import { TerminalBlock } from "./terminal-block";
import { CodeDiff, type DiffLine } from "./code-diff";
import { ReviewableDiff, type DiffHunk } from "./reviewable-diff";
import { FileTree, type FileTreeNode } from "./file-tree";

afterEach(cleanup);

function ControlledToolCall({ running = false, initialOpen = false, requestLabel, resultLabel }: {
  running?: boolean;
  initialOpen?: boolean;
  requestLabel?: string;
  resultLabel?: string;
}) {
  const [open, setOpen] = useState(initialOpen);
  return (
    <ToolCall
      label="Searched files"
      activeLabel="Searching files"
      query="src"
      request="Find the configuration file"
      result="Found src/config.ts"
      requestLabel={requestLabel}
      resultLabel={resultLabel}
      running={running}
      open={open}
      onOpenChange={setOpen}
      className="tool-call-host"
    />
  );
}

const timelineSteps: TimelineStep[] = [
  { verb: "Searching", chip: "src", icon: SearchIcon },
  { verb: "Reading", chip: "config.ts", icon: FileIcon },
  { verb: "Summarizing", chip: "results", icon: FileIcon },
];

function ControlledTimeline({ streaming = false, initialOpen = false, visibleSteps = 3 }: {
  streaming?: boolean;
  initialOpen?: boolean;
  visibleSteps?: number;
}) {
  const [open, setOpen] = useState(initialOpen);
  return (
    <ToolTimeline
      steps={timelineSteps}
      visibleSteps={visibleSteps}
      streaming={streaming}
      open={open}
      onOpenChange={setOpen}
      restingLabel="Research complete"
      activeLabel="Researching"
      stats={[
        { file: "src/config.ts", added: 2, removed: 1 },
        { file: "README.md", added: 0 },
      ]}
      className="timeline-host"
    />
  );
}

describe("donor ToolCall", () => {
  it("keeps request and result behind a controlled disclosure", () => {
    const { container } = render(<ControlledToolCall />);
    const root = container.querySelector('[data-slot="tool-call"]');
    expect(root).not.toBeNull();
    expect(root).toHaveClass("tool-call-host");
    expect(screen.getByText("src")).toBeInTheDocument();
    expect(screen.queryByText("Find the configuration file")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button"));
    expect(screen.getByText("Find the configuration file")).toBeInTheDocument();
    expect(screen.getByText("Found src/config.ts")).toBeInTheDocument();
    expect(screen.getByText("Request")).toBeInTheDocument();
    expect(screen.getByText("Result")).toBeInTheDocument();
    expect(container.querySelector('[data-slot="collapsible-content"]')).not.toBeNull();
    fireEvent.click(screen.getByRole("button"));
    expect(screen.queryByText("Found src/config.ts")).not.toBeInTheDocument();
  });

  it("shows caller-supplied request and result labels beside unchanged data", () => {
    render(<ControlledToolCall initialOpen requestLabel="Anfrage" resultLabel="Ergebnis" />);
    expect(screen.getByText("Anfrage")).toBeInTheDocument();
    expect(screen.getByText("Ergebnis")).toBeInTheDocument();
    expect(screen.queryByText("Request")).not.toBeInTheDocument();
    expect(screen.queryByText("Result")).not.toBeInTheDocument();
    expect(screen.getByText("Find the configuration file")).toBeInTheDocument();
    expect(screen.getByText("Found src/config.ts")).toBeInTheDocument();
  });

  it("keeps each donor English default when only the other label is supplied", () => {
    const { rerender } = render(<ControlledToolCall initialOpen requestLabel="Anfrage" />);
    expect(screen.getByText("Anfrage")).toBeInTheDocument();
    expect(screen.getByText("Result")).toBeInTheDocument();
    rerender(<ControlledToolCall initialOpen resultLabel="Ergebnis" />);
    expect(screen.getByText("Request")).toBeInTheDocument();
    expect(screen.getByText("Ergebnis")).toBeInTheDocument();
    expect(screen.getByText("Found src/config.ts")).toBeInTheDocument();
  });

  it("shows active label while running and keeps completion badge hidden", () => {
    const { container } = render(<ControlledToolCall running initialOpen />);
    expect(screen.getByText("Searching files")).toHaveClass("shimmer");
    expect(screen.getByText("Find the configuration file")).toBeInTheDocument();
    expect(screen.getByText("Found src/config.ts")).toBeInTheDocument();
    expect(container.querySelector(".text-emerald-500")).toBeNull();
  });

  it("shows the completion badge for a completed successful generic call", () => {
    const { container } = render(<ControlledToolCall initialOpen />);
    expect(screen.getByText("Searched files")).toBeInTheDocument();
    expect(container.querySelector(".text-emerald-500")).not.toBeNull();
    expect(screen.getByText("Found src/config.ts")).toBeInTheDocument();
  });

  it("retains the query and result when the parent changes disclosure state", () => {
    const { rerender } = render(<ControlledToolCall running initialOpen />);
    expect(screen.getByText("src")).toBeInTheDocument();
    expect(screen.getByText("Found src/config.ts")).toBeInTheDocument();
    rerender(<ControlledToolCall running={false} initialOpen />);
    expect(screen.getByText("src")).toBeInTheDocument();
    expect(screen.getByText("Found src/config.ts")).toBeInTheDocument();
  });
});

describe("donor ToolTimeline", () => {
  it("reveals ordered steps only when the parent opens it", () => {
    const { container } = render(<ControlledTimeline />);
    expect(container.querySelector('[data-slot="tool-timeline"]')).toHaveClass("timeline-host");
    expect(screen.queryByText("Searching")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button"));
    expect(screen.getByText("Searching")).toBeInTheDocument();
    expect(screen.getByText("Reading")).toBeInTheDocument();
    expect(screen.getByText("Summarizing")).toBeInTheDocument();
    expect(screen.getByText("src")).toBeInTheDocument();
    expect(screen.getByText("config.ts")).toBeInTheDocument();
    expect(screen.getByText("results")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button"));
    expect(screen.queryByText("Searching")).not.toBeInTheDocument();
  });

  it("uses visibleSteps as a bounded progressive reveal", () => {
    const { rerender } = render(<ControlledTimeline initialOpen visibleSteps={1} />);
    expect(screen.getByText("Searching")).toBeInTheDocument();
    expect(screen.queryByText("Reading")).not.toBeInTheDocument();
    expect(screen.queryByText("Summarizing")).not.toBeInTheDocument();
    rerender(<ControlledTimeline initialOpen visibleSteps={2} />);
    expect(screen.getByText("Reading")).toBeInTheDocument();
    expect(screen.queryByText("Summarizing")).not.toBeInTheDocument();
  });

  it("clamps negative, excessive and NaN reveal counts", () => {
    const { rerender } = render(<ControlledTimeline initialOpen visibleSteps={-3} />);
    expect(screen.queryByText("Searching")).not.toBeInTheDocument();
    rerender(<ControlledTimeline initialOpen visibleSteps={Number.NaN} />);
    expect(screen.queryByText("Searching")).not.toBeInTheDocument();
    rerender(<ControlledTimeline initialOpen visibleSteps={99} />);
    expect(screen.getByText("Searching")).toBeInTheDocument();
    expect(screen.getByText("Summarizing")).toBeInTheDocument();
  });

  it("announces active progress without marking the previous step active", () => {
    const { container } = render(<ControlledTimeline streaming initialOpen visibleSteps={2} />);
    expect(screen.getByText("Researching")).toHaveClass("shimmer");
    expect(screen.getByText("Reading")).toHaveClass("shimmer");
    expect(screen.getByText("Searching")).not.toHaveClass("shimmer");
    expect(container.querySelectorAll('[data-slot="collapsible-content"]')).toHaveLength(1);
  });

  it("shows actual per-file additions and removals without inventing missing values", () => {
    render(<ControlledTimeline initialOpen />);
    const first = screen.getByText("src/config.ts").parentElement!;
    expect(within(first).getByText("+2")).toBeInTheDocument();
    expect(within(first).getByText("−1")).toBeInTheDocument();
    const second = screen.getByText("README.md").parentElement!;
    expect(within(second).getByText("+0")).toBeInTheDocument();
    expect(within(second).queryByText(/−/)).not.toBeInTheDocument();
  });

  it("renders the resting label when streaming is finished", () => {
    render(<ControlledTimeline initialOpen />);
    expect(screen.getByText("Research complete")).toBeInTheDocument();
    expect(screen.getByText("Summarizing")).not.toHaveClass("shimmer");
  });
});

describe("donor TerminalBlock", () => {
  it("renders only visible output while a command is running", () => {
    const { container } = render(
      <TerminalBlock command="npm test" lines={["starting", "checking", "complete"]}
        visibleCount={2} done={false} className="terminal-host" data-run-id="run-1" />,
    );
    const root = container.querySelector('[data-slot="terminal-block"]');
    expect(root).toHaveClass("terminal-host");
    expect(root).toHaveAttribute("data-run-id", "run-1");
    expect(screen.getByText("npm test")).toBeInTheDocument();
    expect(screen.getByText("starting")).toBeInTheDocument();
    expect(screen.getByText("checking")).toBeInTheDocument();
    expect(screen.queryByText("complete")).not.toBeInTheDocument();
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
    expect(root?.querySelector('[aria-hidden="true"]')).not.toBeNull();
  });

  it("never invents a zero exit code for an unknown outcome", () => {
    const { container } = render(
      <TerminalBlock command="deploy" lines={["request sent"]} visibleCount={1} done />,
    );
    expect(screen.getByRole("status")).toHaveTextContent("Finished");
    expect(screen.queryByText("exit 0")).not.toBeInTheDocument();
    expect(container.querySelector(".text-emerald-500")).toBeNull();
    expect(container.querySelector('.animate-pulse[aria-hidden="true"]')).toBeNull();
  });

  it("shows a known successful exit code without a running cursor", () => {
    const { container } = render(
      <TerminalBlock command="npm test" lines={["pass"]} visibleCount={1} done exitCode={0} />,
    );
    expect(screen.getByRole("status")).toHaveTextContent("exit 0");
    expect(screen.getByText("pass")).toBeInTheDocument();
    expect(container.querySelector(".text-emerald-500")).not.toBeNull();
    expect(container.querySelector('.animate-pulse[aria-hidden="true"]')).toBeNull();
  });

  it("shows a known failure as a failure", () => {
    const { container } = render(
      <TerminalBlock command="npm test" lines={["failed"]} visibleCount={1} done exitCode={2} />,
    );
    expect(screen.getByRole("status")).toHaveTextContent("exit 2");
    expect(screen.getByText("exit 2")).toHaveClass("text-red-600");
    expect(container.querySelector(".text-red-500")).not.toBeNull();
    expect(container.querySelector(".text-emerald-500")).toBeNull();
  });

  it("uses the ink variant and highlights the actual last output line", () => {
    const { container } = render(
      <TerminalBlock command="ls" lines={["alpha", "beta"]} visibleCount={2}
        done exitCode={0} variant="ink" />,
    );
    const root = container.querySelector('[data-slot="terminal-block"]');
    expect(root).toHaveClass("bg-foreground");
    expect(screen.getByText("beta")).toHaveClass("text-background/90");
    expect(screen.getByText("alpha")).not.toHaveClass("text-background/90");
  });

  it("clamps negative and NaN visible counts without leaking output", () => {
    const { rerender } = render(
      <TerminalBlock command="log" lines={["secret"]} visibleCount={-1} done={false} />,
    );
    expect(screen.queryByText("secret")).not.toBeInTheDocument();
    rerender(<TerminalBlock command="log" lines={["secret"]} visibleCount={Number.NaN} done={false} />);
    expect(screen.queryByText("secret")).not.toBeInTheDocument();
    rerender(<TerminalBlock command="log" lines={["secret"]} visibleCount={99} done={false} />);
    expect(screen.getByText("secret")).toBeInTheDocument();
  });
});

const diffLines: DiffLine[] = [
  { kind: "context", text: "const oldValue = 1;" },
  { kind: "removed", text: "return oldValue;" },
  { kind: "added", text: "return newValue;" },
];

describe("donor CodeDiff", () => {
  it("renders the filename, supplied totals and every line kind", () => {
    const { container } = render(
      <CodeDiff filename="src/value.ts" additions={4} deletions={2}
        lines={diffLines} cycle={0} data-diff-id="diff-1" />,
    );
    const root = container.querySelector('[data-slot="code-diff"]');
    expect(root).toHaveAttribute("data-diff-id", "diff-1");
    expect(screen.getByText("src/value.ts")).toBeInTheDocument();
    expect(screen.getByText("+4")).toBeInTheDocument();
    expect(screen.getByText("−2")).toBeInTheDocument();
    expect(screen.getByText("const oldValue = 1;").parentElement).toHaveClass("text-foreground/45");
    expect(screen.getByText("return oldValue;").parentElement).toHaveClass("text-red-700");
    expect(screen.getByText("return newValue;").parentElement).toHaveClass("text-emerald-700");
    expect(root?.querySelector(".overflow-x-auto")).not.toBeNull();
    expect(root?.querySelector(".w-max")).not.toBeNull();
  });

  it("keeps gutter symbols paired with their actual source lines", () => {
    const { container } = render(
      <CodeDiff filename="src/value.ts" additions={1} deletions={1}
        lines={diffLines} cycle={0} />,
    );
    const rows = container.querySelectorAll('[data-slot="code-diff"] .whitespace-pre');
    expect(rows).toHaveLength(3);
    expect(rows[0]).toHaveTextContent("const oldValue = 1;");
    expect(rows[0]).not.toHaveTextContent("+");
    expect(rows[1]).toHaveTextContent("−return oldValue;");
    expect(rows[2]).toHaveTextContent("+return newValue;");
    expect(rows[1]).toHaveClass("bg-red-500/10");
    expect(rows[2]).toHaveClass("bg-emerald-500/10");
  });

  it("uses ordered animation delays and resets rows when the cycle changes", () => {
    const { container, rerender } = render(
      <CodeDiff filename="src/value.ts" additions={1} deletions={1}
        lines={diffLines} cycle={0} />,
    );
    const first = screen.getByText("const oldValue = 1;").parentElement!;
    const second = screen.getByText("return oldValue;").parentElement!;
    const third = screen.getByText("return newValue;").parentElement!;
    expect(first).toHaveStyle({ animationDelay: "0ms" });
    expect(second).toHaveStyle({ animationDelay: "60ms" });
    expect(third).toHaveStyle({ animationDelay: "120ms" });
    rerender(<CodeDiff filename="src/value.ts" additions={1} deletions={1}
      lines={diffLines} cycle={1} />);
    expect(screen.getByText("const oldValue = 1;").parentElement).not.toBe(first);
    expect(container.querySelectorAll('[data-slot="code-diff"] .whitespace-pre')).toHaveLength(3);
  });

  it("shows an empty diff without inventing change lines", () => {
    const { container } = render(
      <CodeDiff filename="empty.ts" additions={0} deletions={0}
        lines={[]} cycle={0} className="custom-diff" />,
    );
    expect(container.querySelector('[data-slot="code-diff"]')).toHaveClass("custom-diff");
    expect(screen.getByText("empty.ts")).toBeInTheDocument();
    expect(screen.getByText("+0")).toBeInTheDocument();
    expect(screen.getByText("−0")).toBeInTheDocument();
    expect(container.querySelectorAll(".whitespace-pre")).toHaveLength(0);
  });
});

const initialHunks: DiffHunk[] = [
  {
    id: "hunk-a",
    range: "@@ -1,2 +1,2 @@",
    decision: "pending",
    lines: [
      { kind: "context", text: "function add() {" },
      { kind: "removed", text: "return 1;" },
      { kind: "added", text: "return 2;" },
    ],
  },
  {
    id: "hunk-b",
    range: "@@ -8,1 +8,1 @@",
    decision: "pending",
    lines: [{ kind: "added", text: "export { add };" }],
  },
];

function ReviewHarness({ allowKeep = true, allowDiscard = true, allowApply = true }: {
  allowKeep?: boolean;
  allowDiscard?: boolean;
  allowApply?: boolean;
}) {
  const [hunks, setHunks] = useState<DiffHunk[]>(initialHunks);
  const [applied, setApplied] = useState(false);
  const decide = (id: string, decision: DiffHunk["decision"]) => {
    setHunks((current) => current.map((hunk) =>
      hunk.id === id ? { ...hunk, decision } : hunk));
  };
  return (
    <>
      <ReviewableDiff
        filename="src/add.ts"
        hunks={hunks}
        onKeep={allowKeep ? (id) => decide(id, "kept") : undefined}
        onDiscard={allowDiscard ? (id) => decide(id, "discarded") : undefined}
        onApply={allowApply ? () => setApplied(true) : undefined}
        data-review-id="review-1"
      />
      {applied && <output data-testid="applied">Applied reviewed diff</output>}
    </>
  );
}

describe("donor ReviewableDiff", () => {
  it("renders truthful pending state and disables Apply until review completes", () => {
    const { container } = render(<ReviewHarness />);
    const root = container.querySelector('[data-slot="reviewable-diff"]');
    expect(root).toHaveAttribute("data-review-id", "review-1");
    expect(screen.getByText("src/add.ts")).toBeInTheDocument();
    expect(screen.getByText("0 of 2 kept")).toBeInTheDocument();
    expect(screen.getByText("2 left to review")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Apply 0" })).toBeDisabled();
    expect(screen.queryByTestId("applied")).not.toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: /^Keep hunk/ })).toHaveLength(2);
  });

  it("routes each Keep action to its stable hunk id", () => {
    render(<ReviewHarness />);
    fireEvent.click(screen.getByRole("button", { name: "Keep hunk @@ -1,2 +1,2 @@" }));
    expect(screen.getByText("1 of 2 kept")).toBeInTheDocument();
    expect(screen.getByText("1 left to review")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Apply 1" })).toBeDisabled();
    expect(screen.queryByRole("button", { name: "Keep hunk @@ -1,2 +1,2 @@" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Keep hunk @@ -8,1 +8,1 @@" })).toBeInTheDocument();
  });

  it("routes Discard to the selected hunk without changing another hunk", () => {
    const { container } = render(<ReviewHarness />);
    fireEvent.click(screen.getByRole("button", { name: "Discard hunk @@ -8,1 +8,1 @@" }));
    expect(screen.getByText("0 of 2 kept")).toBeInTheDocument();
    expect(screen.getByText("1 left to review")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Keep hunk @@ -1,2 +1,2 @@" })).toBeInTheDocument();
    const discarded = screen.getByText("discarded").closest(".border-t");
    expect(discarded).toHaveClass("opacity-40");
    expect(container.querySelectorAll(".opacity-40")).toHaveLength(1);
  });

  it("enables Apply only when the actual reviewed state has no pending hunk", () => {
    render(<ReviewHarness />);
    fireEvent.click(screen.getByRole("button", { name: "Keep hunk @@ -1,2 +1,2 @@" }));
    fireEvent.click(screen.getByRole("button", { name: "Discard hunk @@ -8,1 +8,1 @@" }));
    expect(screen.getByText("All reviewed")).toBeInTheDocument();
    expect(screen.getByText("1 of 2 kept")).toBeInTheDocument();
    const apply = screen.getByRole("button", { name: "Apply 1" });
    expect(apply).not.toBeDisabled();
    fireEvent.click(apply);
    expect(screen.getByTestId("applied")).toHaveTextContent("Applied reviewed diff");
  });

  it("shows diff line kinds and gutter marks inside each hunk", () => {
    const { container } = render(<ReviewHarness />);
    const rows = container.querySelectorAll('[data-slot="reviewable-diff"] .whitespace-pre');
    expect(rows).toHaveLength(4);
    expect(rows[0]).toHaveTextContent("function add() {");
    expect(rows[1]).toHaveTextContent("−return 1;");
    expect(rows[2]).toHaveTextContent("+return 2;");
    expect(rows[3]).toHaveTextContent("+export { add };");
    expect(rows[0]).toHaveClass("text-foreground/40");
    expect(rows[1]).toHaveClass("text-red-700");
    expect(rows[2]).toHaveClass("text-emerald-700");
  });

  it("shows only actions supplied by the caller", () => {
    render(<ReviewHarness allowKeep={false} allowApply={false} />);
    expect(screen.getAllByRole("button", { name: /^Discard hunk/ })).toHaveLength(2);
    expect(screen.queryByRole("button", { name: /^Keep hunk/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^Apply/ })).not.toBeInTheDocument();
  });

  it("does not present unavailable review actions as working controls", () => {
    render(<ReviewHarness allowKeep={false} allowDiscard={false} />);
    expect(screen.queryByRole("button", { name: /^Keep hunk/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^Discard hunk/ })).not.toBeInTheDocument();
    expect(screen.getAllByText("pending")).toHaveLength(2);
    expect(screen.getByRole("button", { name: "Apply 0" })).toBeDisabled();
  });
});

const treeNodes: FileTreeNode[] = [
  { path: "src", name: "src", depth: 0, kind: "folder" },
  { path: "src/app.ts", name: "app.ts", depth: 1, kind: "file", additions: 3, deletions: 1 },
  { path: "src/utils", name: "utils", depth: 1, kind: "folder" },
  { path: "src/utils/math.ts", name: "math.ts", depth: 2, kind: "file", additions: 0, deletions: 2 },
  { path: "README.md", name: "README.md", depth: 0, kind: "file" },
];

describe("donor FileTree", () => {
  it("reports file count and caller supplied totals from the full tree", () => {
    const { container } = render(
      <FileTree nodes={treeNodes} visibleCount={5} totalAdditions={3}
        totalDeletions={3} data-tree-id="tree-1" className="tree-host" />,
    );
    const root = container.querySelector('[data-slot="file-tree"]');
    expect(root).toHaveAttribute("data-tree-id", "tree-1");
    expect(root).toHaveClass("tree-host");
    expect(screen.getByText("3 files changed")).toBeInTheDocument();
    expect(screen.getAllByText("+3")).toHaveLength(2);
    expect(screen.getByText("−3")).toBeInTheDocument();
    expect(screen.getByText("app.ts")).toBeInTheDocument();
    expect(screen.getByText("math.ts")).toBeInTheDocument();
    expect(screen.getByText("README.md")).toBeInTheDocument();
  });

  it("reveals only the requested prefix of folder and file rows", () => {
    const { rerender } = render(
      <FileTree nodes={treeNodes} visibleCount={2} totalAdditions={3} totalDeletions={3} />,
    );
    expect(screen.getByText("src")).toBeInTheDocument();
    expect(screen.getByText("app.ts")).toBeInTheDocument();
    expect(screen.queryByText("utils")).not.toBeInTheDocument();
    expect(screen.queryByText("math.ts")).not.toBeInTheDocument();
    expect(screen.getByText("3 files changed")).toBeInTheDocument();
    rerender(<FileTree nodes={treeNodes} visibleCount={4}
      totalAdditions={3} totalDeletions={3} />);
    expect(screen.getByText("utils")).toBeInTheDocument();
    expect(screen.getByText("math.ts")).toBeInTheDocument();
    expect(screen.queryByText("README.md")).not.toBeInTheDocument();
  });

  it("clamps invalid reveal counts and preserves totals without fabricated rows", () => {
    const { rerender } = render(
      <FileTree nodes={treeNodes} visibleCount={-2} totalAdditions={3} totalDeletions={3} />,
    );
    expect(screen.queryByText("src")).not.toBeInTheDocument();
    expect(screen.getByText("3 files changed")).toBeInTheDocument();
    rerender(<FileTree nodes={treeNodes} visibleCount={Number.NaN}
      totalAdditions={3} totalDeletions={3} />);
    expect(screen.queryByText("src")).not.toBeInTheDocument();
    rerender(<FileTree nodes={treeNodes} visibleCount={99}
      totalAdditions={3} totalDeletions={3} />);
    expect(screen.getByText("README.md")).toBeInTheDocument();
  });

  it("uses node depth for nesting and labels folders separately from files", () => {
    const { container } = render(
      <FileTree nodes={treeNodes} visibleCount={5} totalAdditions={3} totalDeletions={3} />,
    );
    const srcRow = screen.getByText("src").parentElement!;
    const appRow = screen.getByText("app.ts").parentElement!;
    const mathRow = screen.getByText("math.ts").parentElement!;
    expect(srcRow.style.paddingInlineStart).toBe("0.25rem");
    expect(appRow.style.paddingInlineStart).toBe("1.1rem");
    expect(mathRow.style.paddingInlineStart).toBe("1.95rem");
    expect(container.querySelectorAll(".lucide-folder")).toHaveLength(2);
    expect(container.querySelectorAll(".lucide-file")).toHaveLength(3);
  });

  it("omits absent and zero per-file statistics while preserving real removals", () => {
    render(<FileTree nodes={treeNodes} visibleCount={5}
      totalAdditions={3} totalDeletions={3} />);
    const appRow = screen.getByText("app.ts").parentElement!;
    const mathRow = screen.getByText("math.ts").parentElement!;
    const readmeRow = screen.getByText("README.md").parentElement!;
    expect(within(appRow).getByText("+3")).toBeInTheDocument();
    expect(within(appRow).getByText("−1")).toBeInTheDocument();
    expect(within(mathRow).queryByText("+0")).not.toBeInTheDocument();
    expect(within(mathRow).getByText("−2")).toBeInTheDocument();
    expect(within(readmeRow).queryByText(/[+−]/)).not.toBeInTheDocument();
  });

  it("renders an empty tree without inventing changed files", () => {
    const { container } = render(
      <FileTree nodes={[]} visibleCount={10} totalAdditions={0} totalDeletions={0} />,
    );
    expect(screen.getByText("0 files changed")).toBeInTheDocument();
    expect(screen.getByText("+0")).toBeInTheDocument();
    expect(screen.getByText("−0")).toBeInTheDocument();
    expect(container.querySelectorAll('[data-slot="file-tree"] .truncate')).toHaveLength(0);
  });
});
