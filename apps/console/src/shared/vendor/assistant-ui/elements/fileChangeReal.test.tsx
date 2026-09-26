import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { FileTree, type FileTreeNode } from "./file-tree";
import { ReviewableDiff, type DiffHunk } from "./reviewable-diff";

const sharedLine = "import { answer } from './source';";
const before = `${sharedLine}\nexport const value = 1;\n`;
const after = `${sharedLine}\nexport const value = 2;\n`;
const truncatedAfter = `${after}… [truncated]`;
const changedPath = "src/answer.ts";
const folder: FileTreeNode = { path: "src", name: "src", depth: 0, kind: "folder" };
const file: FileTreeNode = {
  path: changedPath, name: changedPath, depth: 0, kind: "file",
  additions: 1, deletions: 1, snapshotComplete: true,
};
const appliedHunk: DiffHunk = {
  id: changedPath,
  range: "@@ -1,2 +1,2 @@",
  lines: [
    { kind: "context", text: before.trimEnd().split("\n")[0] },
    { kind: "removed", text: before.trimEnd().split("\n")[1] },
    { kind: "added", text: after.trimEnd().split("\n")[1] },
  ],
};

describe("FileTree with persisted file-change snapshots", () => {
  it("shows the real path and complete counts, opening only a file through its exact path", () => {
    const onFileClick = vi.fn();
    render(<FileTree nodes={[folder, file]} visibleCount={2} totalAdditions={1}
      totalDeletions={1} onFileClick={onFileClick} />);
    expect(screen.getByText("1 files changed")).toBeInTheDocument();
    expect(screen.getAllByText("+1")).toHaveLength(2);
    expect(screen.getAllByText("−1")).toHaveLength(2);
    expect(screen.getByText("src")).toBeInTheDocument();
    const button = screen.getByRole("button", { name: /src\/answer\.ts/ });
    expect(button).toHaveAttribute("title", changedPath);
    button.focus();
    expect(document.activeElement).toBe(button);
    fireEvent.click(button);
    expect(onFileClick.mock.calls).toEqual([[changedPath]]);
  });

  it("keeps a truncated path visible but suppresses its counts and all supplied totals", () => {
    expect(truncatedAfter.endsWith("… [truncated]")).toBe(true);
    const incomplete = { ...file, path: "src/other.ts", name: "src/other.ts", snapshotComplete: false };
    render(<FileTree nodes={[file, incomplete]} visibleCount={2} totalAdditions={3} totalDeletions={3} />);
    expect(screen.getByText("2 files changed")).toBeInTheDocument();
    expect(screen.getByText("src/answer.ts")).toBeInTheDocument();
    expect(screen.getByText("src/other.ts")).toBeInTheDocument();
    expect(screen.queryByText("+3")).toBeNull();
    expect(screen.queryByText("−3")).toBeNull();
    const incompleteRow = screen.getByText("src/other.ts").parentElement!;
    expect(within(incompleteRow).queryByText(/[+−]\d/)).toBeNull();
    const completeRow = screen.getByText("src/answer.ts").parentElement!;
    expect(within(completeRow).getByText("+1")).toBeInTheDocument();
  });

  it("hides unknown totals while preserving known per-file counts", () => {
    const { rerender } = render(<FileTree nodes={[file]} visibleCount={1} />);
    expect(screen.getByText(changedPath)).toBeInTheDocument();
    expect(screen.getByText("+1")).toBeInTheDocument();
    expect(screen.getByText("−1")).toBeInTheDocument();
    expect(screen.queryByText("+0")).toBeNull();
    rerender(<FileTree nodes={[file]} visibleCount={1} totalAdditions={0} />);
    expect(screen.getByText("+0")).toBeInTheDocument();
    expect(screen.queryByText("−0")).toBeNull();
    rerender(<FileTree nodes={[file]} visibleCount={1} totalDeletions={0} />);
    expect(screen.getByText("−0")).toBeInTheDocument();
    expect(screen.queryByText("+0")).toBeNull();
  });

  it("keeps donor read-only rows when no open route exists", () => {
    const { container, rerender } = render(<FileTree nodes={[folder, file]} visibleCount={2}
      totalAdditions={1} totalDeletions={1} />);
    expect(screen.queryByRole("button")).toBeNull();
    expect(container.querySelectorAll('[data-slot="file-tree"] .truncate')).toHaveLength(2);
    rerender(<FileTree nodes={[]} visibleCount={0} totalAdditions={0} totalDeletions={0} />);
    expect(screen.getByText("0 files changed")).toBeInTheDocument();
    expect(screen.getByText("+0")).toBeInTheDocument();
    expect(screen.getByText("−0")).toBeInTheDocument();
  });
});

describe("ReviewableDiff for already-applied persisted snapshots", () => {
  it("shows actual before/after lines and Applied without fabricating a review decision", () => {
    const onKeep = vi.fn();
    const onDiscard = vi.fn();
    const onApply = vi.fn();
    const { container } = render(<ReviewableDiff filename={changedPath} hunks={[appliedHunk]}
      mode="applied" onKeep={onKeep} onDiscard={onDiscard} onApply={onApply} />);
    const diff = container.querySelector('[data-slot="reviewable-diff"]') as HTMLElement;
    expect(within(diff).getByText(changedPath)).toBeInTheDocument();
    expect(within(diff).getByText("Applied")).toBeInTheDocument();
    expect(within(diff).getByText(sharedLine)).toBeInTheDocument();
    expect(within(diff).getByText("export const value = 1;")).toBeInTheDocument();
    expect(within(diff).getByText("export const value = 2;")).toBeInTheDocument();
    expect(within(diff).getByText("@@ -1,2 +1,2 @@")).toBeInTheDocument();
    expect(within(diff).queryByRole("button")).toBeNull();
    expect(diff.textContent).not.toMatch(/pending|kept|discarded|review|Apply \d/i);
    expect(onKeep).not.toHaveBeenCalled();
    expect(onDiscard).not.toHaveBeenCalled();
    expect(onApply).not.toHaveBeenCalled();
  });

  it("never dims or labels a completed hunk based on stale review metadata", () => {
    const { container } = render(<ReviewableDiff filename={changedPath}
      hunks={[{ ...appliedHunk, decision: "discarded" }]} mode="applied" />);
    expect(screen.getByText("Applied")).toBeInTheDocument();
    expect(screen.queryByText("discarded")).toBeNull();
    expect(container.querySelector(".opacity-40")).toBeNull();
    expect(screen.queryByRole("button")).toBeNull();
  });

  it("keeps the original donor review mode and real callbacks as the default", () => {
    const onKeep = vi.fn();
    const onDiscard = vi.fn();
    const onApply = vi.fn();
    const pending: DiffHunk = { ...appliedHunk, decision: "pending" };
    render(<ReviewableDiff filename={changedPath} hunks={[pending]} onKeep={onKeep}
      onDiscard={onDiscard} onApply={onApply} />);
    expect(screen.getByText("0 of 1 kept")).toBeInTheDocument();
    expect(screen.getByText("1 left to review")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Keep hunk @@ -1,2 +1,2 @@" }));
    fireEvent.click(screen.getByRole("button", { name: "Discard hunk @@ -1,2 +1,2 @@" }));
    expect(screen.getByRole("button", { name: "Apply 0" })).toBeDisabled();
    expect(onKeep).toHaveBeenCalledWith(changedPath);
    expect(onDiscard).toHaveBeenCalledWith(changedPath);
    expect(onApply).not.toHaveBeenCalled();
  });

  it("preserves reviewed donor actions without showing an applied state", () => {
    const onApply = vi.fn();
    const { container } = render(<ReviewableDiff filename={changedPath}
      hunks={[{ ...appliedHunk, decision: "kept" }]} onApply={onApply} />);
    expect(screen.getByText("1 of 1 kept")).toBeInTheDocument();
    expect(screen.getByText("All reviewed")).toBeInTheDocument();
    expect(screen.queryByText("Applied")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Apply 1" }));
    expect(onApply).toHaveBeenCalledTimes(1);
    expect(container.querySelector(".opacity-40")).toBeNull();
  });

  it("retains donor discarded styling only for a real review decision", () => {
    const { container } = render(<ReviewableDiff filename={changedPath}
      hunks={[{ ...appliedHunk, decision: "discarded" }]} />);
    expect(screen.getByText("discarded")).toBeInTheDocument();
    expect(screen.getByText("All reviewed")).toBeInTheDocument();
    expect(container.querySelector(".opacity-40")).not.toBeNull();
  });

  it("offers only the supplied review action for a pending hunk", () => {
    const onDiscard = vi.fn();
    render(<ReviewableDiff filename={changedPath}
      hunks={[{ ...appliedHunk, decision: "pending" }]} onDiscard={onDiscard} />);
    expect(screen.getByRole("button", { name: "Discard hunk @@ -1,2 +1,2 @@" })).toBeEnabled();
    expect(screen.queryByRole("button", { name: "Keep hunk @@ -1,2 +1,2 @@" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Discard hunk @@ -1,2 +1,2 @@" }));
    expect(onDiscard).toHaveBeenCalledWith(changedPath);
  });
});
