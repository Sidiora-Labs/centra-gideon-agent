import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { MessagePair } from "./message-pair";
import { MessageBranches } from "./message-branches";
import { MessageActions } from "./message-actions";
import { ErrorState } from "./error-state";
import { MessageQueue } from "./message-queue";
import { EditMessage } from "./edit-message";

afterEach(cleanup);

describe("MessagePair donor transcript treatment", () => {
  it("shows the user turn and only the requested assistant words", () => {
    const { container } = render(<MessagePair userMessage="How does this work?"
      words={["It", "works", "in", "steps"]} visibleWords={2} streaming={false} />);
    const pair = container.querySelector('[data-slot="message-pair"]');
    expect(pair).not.toBeNull();
    expect(within(pair as HTMLElement).getByText("How does this work?")).toBeTruthy();
    expect(within(pair as HTMLElement).getByText("It")).toBeTruthy();
    expect(within(pair as HTMLElement).getByText("works")).toBeTruthy();
    expect(within(pair as HTMLElement).queryByText("steps")).toBeNull();
    expect(pair?.textContent).not.toContain("in steps");
  });

  it("keeps the donor flat message variant and forwards DOM attributes", () => {
    const { container } = render(<MessagePair userMessage="hello" words={["world"]}
      visibleWords={1} streaming={false} variant="flat" data-testid="flat-pair" className="consumer-class" />);
    const pair = screen.getByTestId("flat-pair");
    expect(pair.getAttribute("data-slot")).toBe("message-pair");
    expect(pair.className).toContain("consumer-class");
    const user = pair.querySelector("p");
    expect(user?.className).toContain("text-end");
    expect(user?.className).not.toContain("rounded-2xl");
    expect(container.querySelectorAll("p")).toHaveLength(2);
  });

  it("shows streaming emphasis and cursor only while words are arriving", () => {
    const { container, rerender } = render(<MessagePair userMessage="prompt" words={["one", "two", "three"]}
      visibleWords={2} streaming />);
    expect(container.querySelector('[aria-hidden="true"]')).not.toBeNull();
    expect(screen.getByText("one").className).toContain("text-blue-500");
    expect(screen.getByText("two").className).toContain("text-blue-500");
    rerender(<MessagePair userMessage="prompt" words={["one", "two", "three"]}
      visibleWords={3} streaming={false} />);
    expect(container.querySelector('[aria-hidden="true"]')).toBeNull();
    expect(screen.getByText("three").className).not.toContain("text-blue-500");
  });

  it("does not show a dead cursor for an empty streamed response", () => {
    const { container } = render(<MessagePair userMessage="prompt" words={[]}
      visibleWords={0} streaming />);
    expect(container.querySelector('[aria-hidden="true"]')).toBeNull();
    expect(screen.getByText("prompt")).toBeTruthy();
  });

  it("omits both action controls when no real consumer callbacks exist", () => {
    render(<MessagePair userMessage="prompt" words={["answer"]} visibleWords={1} streaming={false} />);
    expect(screen.queryByRole("button", { name: "Copy response" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Regenerate response" })).toBeNull();
  });

  it("calls only the supplied copy action", () => {
    const onCopy = vi.fn();
    render(<MessagePair userMessage="prompt" words={["answer"]} visibleWords={1}
      streaming={false} onCopy={onCopy} />);
    fireEvent.click(screen.getByRole("button", { name: "Copy response" }));
    expect(onCopy).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole("button", { name: "Regenerate response" })).toBeNull();
  });

  it("calls only the supplied regeneration action", () => {
    const onRegenerate = vi.fn();
    render(<MessagePair userMessage="prompt" words={["answer"]} visibleWords={1}
      streaming={false} onRegenerate={onRegenerate} />);
    fireEvent.click(screen.getByRole("button", { name: "Regenerate response" }));
    expect(onRegenerate).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole("button", { name: "Copy response" })).toBeNull();
  });

  it("keeps copy and regeneration as separate actions when both are wired", () => {
    const onCopy = vi.fn();
    const onRegenerate = vi.fn();
    render(<MessagePair userMessage="prompt" words={["answer"]} visibleWords={1}
      streaming={false} onCopy={onCopy} onRegenerate={onRegenerate} />);
    fireEvent.click(screen.getByRole("button", { name: "Copy response" }));
    fireEvent.click(screen.getByRole("button", { name: "Regenerate response" }));
    expect(onCopy).toHaveBeenCalledTimes(1);
    expect(onRegenerate).toHaveBeenCalledTimes(1);
  });

  it("keeps copy scoped to the currently supplied response callback after rerender", () => {
    const firstCopy = vi.fn();
    const secondCopy = vi.fn();
    const { rerender } = render(<MessagePair userMessage="First" words={["first"]}
      visibleWords={1} streaming={false} onCopy={firstCopy} />);
    fireEvent.click(screen.getByRole("button", { name: "Copy response" }));
    rerender(<MessagePair userMessage="Second" words={["second"]}
      visibleWords={1} streaming={false} onCopy={secondCopy} />);
    fireEvent.click(screen.getByRole("button", { name: "Copy response" }));
    expect(firstCopy).toHaveBeenCalledTimes(1);
    expect(secondCopy).toHaveBeenCalledTimes(1);
    expect(screen.getByText("second")).toBeTruthy();
    expect(screen.queryByText("first")).toBeNull();
  });

  it("bounds a negative or oversized visible word count to the donor word list", () => {
    const { rerender } = render(<MessagePair userMessage="Question" words={["alpha", "beta"]}
      visibleWords={-4} streaming={false} />);
    expect(screen.queryByText("alpha")).toBeNull();
    rerender(<MessagePair userMessage="Question" words={["alpha", "beta"]}
      visibleWords={20} streaming={false} />);
    expect(screen.getByText("alpha")).toBeTruthy();
    expect(screen.getByText("beta")).toBeTruthy();
  });

  it("keeps the donor bubble treatment for the default user message", () => {
    const { container } = render(<MessagePair userMessage="A user question"
      words={["A", "response"]} visibleWords={2} streaming={false} />);
    const user = container.querySelector('[data-slot="message-pair"] > p');
    expect(user?.textContent).toBe("A user question");
    expect(user?.className).toContain("rounded-2xl");
    expect(user?.className).toContain("self-end");
    expect(screen.getByText("A")).toBeTruthy();
    expect(screen.getByText("response")).toBeTruthy();
  });
});

describe("MessageBranches donor variant navigation", () => {
  it("shows the active response and its position", () => {
    render(<MessageBranches variants={["first", "second", "third"]} index={1}
      onIndexChange={vi.fn()} />);
    expect(screen.getByText("second")).toBeTruthy();
    expect(screen.queryByText("first")).toBeNull();
    expect(screen.getByText("2 / 3")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Show previous response" }).hasAttribute("disabled")).toBe(false);
    expect(screen.getByRole("button", { name: "Show next response" }).hasAttribute("disabled")).toBe(false);
  });

  it("asks the owner to move to adjacent variants without owning history", () => {
    const onIndexChange = vi.fn();
    render(<MessageBranches variants={["first", "second", "third"]} index={1}
      onIndexChange={onIndexChange} />);
    fireEvent.click(screen.getByRole("button", { name: "Show previous response" }));
    fireEvent.click(screen.getByRole("button", { name: "Show next response" }));
    expect(onIndexChange.mock.calls).toEqual([[0], [2]]);
    expect(screen.getByText("second")).toBeTruthy();
  });

  it("wraps from the first variant to the last", () => {
    const onIndexChange = vi.fn();
    render(<MessageBranches variants={["first", "second", "third"]} index={0}
      onIndexChange={onIndexChange} />);
    fireEvent.click(screen.getByRole("button", { name: "Show previous response" }));
    expect(onIndexChange).toHaveBeenCalledWith(2);
  });

  it("wraps from the last variant to the first", () => {
    const onIndexChange = vi.fn();
    render(<MessageBranches variants={["first", "second", "third"]} index={2}
      onIndexChange={onIndexChange} />);
    fireEvent.click(screen.getByRole("button", { name: "Show next response" }));
    expect(onIndexChange).toHaveBeenCalledWith(0);
  });

  it("prevents navigation when only one real variant exists", () => {
    const onIndexChange = vi.fn();
    render(<MessageBranches variants={["only answer"]} index={0} onIndexChange={onIndexChange} />);
    expect(screen.getByText("only answer")).toBeTruthy();
    expect(screen.getByText("1 / 1")).toBeTruthy();
    for (const label of ["Show previous response", "Show next response"]) {
      const button = screen.getByRole("button", { name: label });
      expect(button.hasAttribute("disabled")).toBe(true);
      fireEvent.click(button);
    }
    expect(onIndexChange).not.toHaveBeenCalled();
  });

  it("announces zero variants and does not invent a response", () => {
    const onIndexChange = vi.fn();
    const { container } = render(<MessageBranches variants={[]} index={0} onIndexChange={onIndexChange} />);
    expect(screen.getByText("0 / 0")).toBeTruthy();
    expect(container.querySelector('[data-slot="message-branches"] p')?.textContent).toBe("");
    expect(screen.getByRole("button", { name: "Show next response" }).hasAttribute("disabled")).toBe(true);
    expect(onIndexChange).not.toHaveBeenCalled();
  });

  it("updates the selected branch only when the real owner changes the index prop", () => {
    const onIndexChange = vi.fn();
    const variants = ["first response", "second response"];
    const { rerender } = render(<MessageBranches variants={variants} index={0}
      onIndexChange={onIndexChange} />);
    fireEvent.click(screen.getByRole("button", { name: "Show next response" }));
    expect(onIndexChange).toHaveBeenCalledWith(1);
    expect(screen.getByText("first response")).toBeTruthy();
    rerender(<MessageBranches variants={variants} index={1} onIndexChange={onIndexChange} />);
    expect(screen.getByText("second response")).toBeTruthy();
    expect(screen.queryByText("first response")).toBeNull();
    expect(screen.getByText("2 / 2")).toBeTruthy();
  });

  it("forwards consumer attributes through the donor branch slot", () => {
    render(<MessageBranches variants={["first", "second"]} index={0}
      onIndexChange={vi.fn()} data-testid="branches" className="consumer-branch" />);
    const slot = screen.getByTestId("branches");
    expect(slot.getAttribute("data-slot")).toBe("message-branches");
    expect(slot.className).toContain("consumer-branch");
    expect(within(slot).getByText("first")).toBeTruthy();
    expect(within(slot).getByText("1 / 2")).toBeTruthy();
    expect(within(slot).getAllByRole("button")).toHaveLength(2);
  });
});

describe("MessageActions donor response controls", () => {
  const callbacks = () => ({ onCopy: vi.fn(), onReactionChange: vi.fn(),
    onRegenerate: vi.fn(), onMore: vi.fn() });

  it("shows the original action set with a real handler for each control", () => {
    const actions = callbacks();
    render(<MessageActions copied={false} reaction={null} regenerating={false} {...actions} />);
    const labels = ["Copy response", "Mark response helpful", "Mark response unhelpful",
      "Regenerate response", "More response actions"];
    for (const label of labels) expect(screen.getByRole("button", { name: label })).toBeTruthy();
    for (const label of labels) fireEvent.click(screen.getByRole("button", { name: label }));
    expect(actions.onCopy).toHaveBeenCalledTimes(1);
    expect(actions.onReactionChange.mock.calls).toEqual([["up"], ["down"]]);
    expect(actions.onRegenerate).toHaveBeenCalledTimes(1);
    expect(actions.onMore).toHaveBeenCalledTimes(1);
  });

  it("switches the copy label to its confirmed state", () => {
    const actions = callbacks();
    render(<MessageActions copied reaction={null} regenerating={false} {...actions} />);
    expect(screen.getByRole("button", { name: "Copied response" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Copy response" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Copied response" }));
    expect(actions.onCopy).toHaveBeenCalledTimes(1);
  });

  it("toggles an already helpful reaction back to neutral", () => {
    const actions = callbacks();
    render(<MessageActions copied={false} reaction="up" regenerating={false} {...actions} />);
    const button = screen.getByRole("button", { name: "Mark response helpful" });
    expect(button.getAttribute("aria-pressed")).toBe("true");
    fireEvent.click(button);
    expect(actions.onReactionChange).toHaveBeenCalledWith(null);
    expect(screen.getByRole("button", { name: "Mark response unhelpful" }).getAttribute("aria-pressed")).toBe("false");
  });

  it("toggles an already unhelpful reaction back to neutral", () => {
    const actions = callbacks();
    render(<MessageActions copied={false} reaction="down" regenerating={false} {...actions} />);
    const button = screen.getByRole("button", { name: "Mark response unhelpful" });
    expect(button.getAttribute("aria-pressed")).toBe("true");
    fireEvent.click(button);
    expect(actions.onReactionChange).toHaveBeenCalledWith(null);
    expect(screen.getByRole("button", { name: "Mark response helpful" }).getAttribute("aria-pressed")).toBe("false");
  });

  it("blocks duplicate regeneration while the answer is regenerating", () => {
    const actions = callbacks();
    render(<MessageActions copied={false} reaction={null} regenerating {...actions} />);
    const button = screen.getByRole("button", { name: "Regenerate response" });
    expect(button.hasAttribute("disabled")).toBe(true);
    expect(button.getAttribute("aria-busy")).toBe("true");
    fireEvent.click(button);
    expect(actions.onRegenerate).not.toHaveBeenCalled();
  });

  it("forwards consumer DOM attributes without changing the response action slot", () => {
    const actions = callbacks();
    render(<MessageActions copied={false} reaction={null} regenerating={false}
      data-testid="consumer-actions" className="consumer-class" {...actions} />);
    const group = screen.getByTestId("consumer-actions");
    expect(group.getAttribute("data-slot")).toBe("message-actions");
    expect(group.className).toContain("consumer-class");
    expect(within(group).getAllByRole("button")).toHaveLength(5);
  });

  it("reflects an owner-updated reaction after a callback without inventing local state", () => {
    const actions = callbacks();
    const { rerender } = render(<MessageActions copied={false} reaction={null}
      regenerating={false} {...actions} />);
    fireEvent.click(screen.getByRole("button", { name: "Mark response helpful" }));
    expect(actions.onReactionChange).toHaveBeenCalledWith("up");
    expect(screen.getByRole("button", { name: "Mark response helpful" }).getAttribute("aria-pressed")).toBe("false");
    rerender(<MessageActions copied={false} reaction="up" regenerating={false} {...actions} />);
    expect(screen.getByRole("button", { name: "Mark response helpful" }).getAttribute("aria-pressed")).toBe("true");
    expect(screen.getByRole("button", { name: "Mark response unhelpful" }).getAttribute("aria-pressed")).toBe("false");
  });

  it("returns regeneration to an actionable state only when the owner clears busy", () => {
    const actions = callbacks();
    const { rerender } = render(<MessageActions copied={false} reaction={null}
      regenerating {...actions} />);
    expect(screen.getByRole("button", { name: "Regenerate response" }).hasAttribute("disabled")).toBe(true);
    rerender(<MessageActions copied={false} reaction={null} regenerating={false} {...actions} />);
    const regenerate = screen.getByRole("button", { name: "Regenerate response" });
    expect(regenerate.hasAttribute("disabled")).toBe(false);
    expect(regenerate.getAttribute("aria-busy")).toBe("false");
    fireEvent.click(regenerate);
    expect(actions.onRegenerate).toHaveBeenCalledTimes(1);
  });
});

describe("ErrorState donor retry surface", () => {
  it("presents the real error and calls retry only on request", () => {
    const onRetry = vi.fn();
    render(<ErrorState title="The request failed" detail="The server timed out"
      retrying={false} onRetry={onRetry} />);
    expect(screen.getByRole("alert")).toBeTruthy();
    expect(screen.getByText("The request failed")).toBeTruthy();
    expect(screen.getByText("The server timed out")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(onRetry).toHaveBeenCalledTimes(1);
  });

  it("hides retry when the owner has no retry route", () => {
    render(<ErrorState title="Access denied" detail="Ask the owner" retrying={false} />);
    expect(screen.getByRole("alert")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Retry" })).toBeNull();
    expect(screen.getByText("Ask the owner")).toBeTruthy();
  });

  it("replaces an error with a status while retry is in progress", () => {
    const onRetry = vi.fn();
    render(<ErrorState title="The request failed" detail="The server timed out"
      retrying onRetry={onRetry} />);
    expect(screen.getByRole("status")).toBeTruthy();
    expect(screen.getByText("Retrying")).toBeTruthy();
    expect(screen.queryByRole("alert")).toBeNull();
    expect(screen.queryByRole("button", { name: "Retry" })).toBeNull();
    expect(onRetry).not.toHaveBeenCalled();
  });

  it("returns from retry status to the current error payload", () => {
    const onRetry = vi.fn();
    const { rerender } = render(<ErrorState title="A" detail="First failure" retrying onRetry={onRetry} />);
    rerender(<ErrorState title="B" detail="Second failure" retrying={false} onRetry={onRetry} />);
    expect(screen.getByRole("alert")).toBeTruthy();
    expect(screen.getByText("B")).toBeTruthy();
    expect(screen.getByText("Second failure")).toBeTruthy();
    expect(screen.queryByText("First failure")).toBeNull();
  });

  it("forwards a consumer identifier to the visible alert and status", () => {
    const { rerender } = render(<ErrorState title="Failed" detail="Try later"
      retrying={false} onRetry={vi.fn()} data-testid="failure" className="consumer-error" />);
    let slot = screen.getByTestId("failure");
    expect(slot.getAttribute("data-slot")).toBe("error-state");
    expect(slot.getAttribute("role")).toBe("alert");
    expect(slot.className).toContain("consumer-error");
    rerender(<ErrorState title="Failed" detail="Try later" retrying
      onRetry={vi.fn()} data-testid="failure" className="consumer-error" />);
    slot = screen.getByTestId("failure");
    expect(slot.getAttribute("role")).toBe("status");
    expect(slot.className).toContain("consumer-error");
  });

  it("uses the latest retry callback after an error changes", () => {
    const firstRetry = vi.fn();
    const secondRetry = vi.fn();
    const { rerender } = render(<ErrorState title="First failure" detail="Timeout"
      retrying={false} onRetry={firstRetry} />);
    rerender(<ErrorState title="Second failure" detail="Unavailable"
      retrying={false} onRetry={secondRetry} />);
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(firstRetry).not.toHaveBeenCalled();
    expect(secondRetry).toHaveBeenCalledTimes(1);
    expect(screen.getByText("Second failure")).toBeTruthy();
    expect(screen.queryByText("First failure")).toBeNull();
  });
});

describe("MessageQueue donor run and queue controls", () => {
  const queued = [{ id: "a", text: "Add an example" }, { id: "b", text: "Check the citations" }];

  it("renders the active run and ordered real queued content", () => {
    const { container } = render(<MessageQueue running="Writing the answer" queued={queued} />);
    expect(screen.getByText("Writing the answer")).toBeTruthy();
    expect(screen.getByText("2 queued")).toBeTruthy();
    expect(screen.getByText("sends when this finishes")).toBeTruthy();
    const rows = container.querySelectorAll('[data-slot="message-queue"] li');
    expect(rows).toHaveLength(2);
    expect(rows[0]?.textContent).toContain("Add an example");
    expect(rows[1]?.textContent).toContain("Check the citations");
    expect(rows[0]?.textContent).toContain("1");
    expect(rows[1]?.textContent).toContain("2");
  });

  it("does not imply queued messages when the queue is empty", () => {
    const { container } = render(<MessageQueue running="Writing the answer" queued={[]} />);
    expect(container.querySelectorAll("li")).toHaveLength(0);
    expect(screen.queryByText("0 queued")).toBeNull();
    expect(screen.queryByText("sends when this finishes")).toBeNull();
  });

  it("routes cancellation by stable queue id", () => {
    const onCancel = vi.fn();
    render(<MessageQueue running="Writing" queued={queued} onCancel={onCancel} />);
    fireEvent.click(screen.getByRole("button", { name: 'Remove "Check the citations" from the queue' }));
    expect(onCancel).toHaveBeenCalledWith("b");
    expect(onCancel).toHaveBeenCalledTimes(1);
    expect(screen.getByText("Add an example")).toBeTruthy();
  });

  it("routes editing by stable queue id without pretending the edit is local", () => {
    const onEdit = vi.fn();
    render(<MessageQueue running="Writing" queued={queued} onEdit={onEdit} />);
    fireEvent.click(screen.getByRole("button", { name: 'Edit "Add an example" in the queue' }));
    expect(onEdit).toHaveBeenCalledWith("a");
    expect(onEdit).toHaveBeenCalledTimes(1);
    expect(screen.getByText("Add an example")).toBeTruthy();
  });

  it("routes interruption to the owner of the running response", () => {
    const onInterrupt = vi.fn();
    render(<MessageQueue running="Writing" queued={queued} onInterrupt={onInterrupt} />);
    fireEvent.click(screen.getByRole("button", { name: "Stop current response" }));
    expect(onInterrupt).toHaveBeenCalledTimes(1);
    expect(screen.getByText("2 queued")).toBeTruthy();
  });

  it("does not show cancel, edit, or stop affordances without a real handler", () => {
    render(<MessageQueue running="Writing" queued={queued} />);
    expect(screen.queryByRole("button", { name: /Remove .* from the queue/ })).toBeNull();
    expect(screen.queryByRole("button", { name: /Edit .* in the queue/ })).toBeNull();
    expect(screen.queryByRole("button", { name: "Stop current response" })).toBeNull();
  });

  it("keeps all three actions independent when a consumer supplies them", () => {
    const onCancel = vi.fn();
    const onEdit = vi.fn();
    const onInterrupt = vi.fn();
    render(<MessageQueue running="Writing" queued={queued} onCancel={onCancel}
      onEdit={onEdit} onInterrupt={onInterrupt} />);
    fireEvent.click(screen.getByRole("button", { name: "Stop current response" }));
    fireEvent.click(screen.getByRole("button", { name: 'Edit "Check the citations" in the queue' }));
    fireEvent.click(screen.getByRole("button", { name: 'Remove "Add an example" from the queue' }));
    expect(onInterrupt).toHaveBeenCalledTimes(1);
    expect(onEdit).toHaveBeenCalledWith("b");
    expect(onCancel).toHaveBeenCalledWith("a");
  });

  it("uses updated queue rows and callbacks after the owner removes an item", () => {
    const oldCancel = vi.fn();
    const newCancel = vi.fn();
    const { rerender } = render(<MessageQueue running="Writing" queued={queued}
      onCancel={oldCancel} />);
    fireEvent.click(screen.getByRole("button", { name: 'Remove "Add an example" from the queue' }));
    expect(oldCancel).toHaveBeenCalledWith("a");
    expect(screen.getByText("2 queued")).toBeTruthy();
    rerender(<MessageQueue running="Writing" queued={[queued[1]!]} onCancel={newCancel} />);
    expect(screen.getByText("1 queued")).toBeTruthy();
    expect(screen.queryByText("Add an example")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: 'Remove "Check the citations" from the queue' }));
    expect(newCancel).toHaveBeenCalledWith("b");
    expect(oldCancel).toHaveBeenCalledTimes(1);
  });

  it("keeps queue action controls scoped to the item the user selected", () => {
    const onCancel = vi.fn();
    const onEdit = vi.fn();
    const { container } = render(<MessageQueue running="Writing" queued={queued}
      onCancel={onCancel} onEdit={onEdit} data-testid="queue" />);
    const rows = container.querySelectorAll("li");
    expect(rows).toHaveLength(2);
    fireEvent.click(within(rows[0] as HTMLElement).getByRole("button", { name: /Edit/ }));
    fireEvent.click(within(rows[1] as HTMLElement).getByRole("button", { name: /Remove/ }));
    expect(onEdit.mock.calls).toEqual([["a"]]);
    expect(onCancel.mock.calls).toEqual([["b"]]);
    expect(screen.getByTestId("queue").getAttribute("data-slot")).toBe("message-queue");
  });
});

describe("EditMessage donor revision surface", () => {
  it("opens the owner edit action from the static message", () => {
    const onStartEdit = vi.fn();
    render(<EditMessage value="Original request" discardedReplies={0} editing={false}
      onStartEdit={onStartEdit} />);
    expect(screen.getByText("Original request")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Original request" }));
    expect(onStartEdit).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole("textbox", { name: "Edit your message" })).toBeNull();
  });

  it("does not present an enabled edit action when the owner cannot edit", () => {
    render(<EditMessage value="Immutable request" discardedReplies={0} editing={false} />);
    expect(screen.getByText("Immutable request")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Immutable request" }).hasAttribute("disabled")).toBe(true);
  });

  it("forwards draft changes and save to the owner without mutating props", () => {
    const onValueChange = vi.fn();
    const onSave = vi.fn();
    const onCancel = vi.fn();
    render(<EditMessage value="Original request" discardedReplies={0} editing
      onValueChange={onValueChange} onSave={onSave} onCancel={onCancel} />);
    const editor = screen.getByRole("textbox", { name: "Edit your message" });
    expect((editor as HTMLTextAreaElement).value).toBe("Original request");
    fireEvent.change(editor, { target: { value: "Corrected request" } });
    expect(onValueChange).toHaveBeenCalledWith("Corrected request");
    fireEvent.click(screen.getByRole("button", { name: "Send" }));
    expect(onSave).toHaveBeenCalledTimes(1);
    expect(onCancel).not.toHaveBeenCalled();
  });

  it("keeps cancel separate from sending the corrected request", () => {
    const onValueChange = vi.fn();
    const onSave = vi.fn();
    const onCancel = vi.fn();
    render(<EditMessage value="Original" discardedReplies={0} editing
      onValueChange={onValueChange} onSave={onSave} onCancel={onCancel} />);
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(onCancel).toHaveBeenCalledTimes(1);
    expect(onSave).not.toHaveBeenCalled();
    expect(onValueChange).not.toHaveBeenCalled();
  });

  it("tells the user exactly one downstream reply will be discarded", () => {
    render(<EditMessage value="Original" discardedReplies={1} editing
      onValueChange={vi.fn()} onSave={vi.fn()} onCancel={vi.fn()} />);
    expect(screen.getByText(/sending discards 1 reply/)).toBeTruthy();
    expect(screen.queryByText(/replies/)).toBeNull();
  });

  it("pluralizes the downstream reply count", () => {
    render(<EditMessage value="Original" discardedReplies={3} editing
      onValueChange={vi.fn()} onSave={vi.fn()} onCancel={vi.fn()} />);
    expect(screen.getByText(/sending discards 3 replies/)).toBeTruthy();
    expect(screen.queryByText(/sending discards 3 reply$/)).toBeNull();
  });

  it("does not show a discard warning when there is no downstream reply", () => {
    render(<EditMessage value="Original" discardedReplies={0} editing
      onValueChange={vi.fn()} onSave={vi.fn()} onCancel={vi.fn()} />);
    expect(screen.queryByText(/sending discards/)).toBeNull();
  });

  it("disables unsupported edit actions instead of presenting dead buttons", () => {
    render(<EditMessage value="Original" discardedReplies={0} editing />);
    expect(screen.getByRole("textbox", { name: "Edit your message" }).hasAttribute("disabled")).toBe(true);
    expect(screen.getByRole("button", { name: "Cancel" }).hasAttribute("disabled")).toBe(true);
    expect(screen.getByRole("button", { name: "Send" }).hasAttribute("disabled")).toBe(true);
  });

  it("updates the editor from owner props and stops showing discarded replies after cancel", () => {
    const onValueChange = vi.fn();
    const onSave = vi.fn();
    const onCancel = vi.fn();
    const { rerender } = render(<EditMessage value="First draft" discardedReplies={2} editing
      onValueChange={onValueChange} onSave={onSave} onCancel={onCancel} />);
    expect(screen.getByText(/sending discards 2 replies/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(onCancel).toHaveBeenCalledTimes(1);
    rerender(<EditMessage value="First draft" discardedReplies={2} editing={false}
      onStartEdit={vi.fn()} />);
    expect(screen.queryByRole("textbox", { name: "Edit your message" })).toBeNull();
    expect(screen.queryByText(/sending discards/)).toBeNull();
    expect(screen.getByText("First draft")).toBeTruthy();
  });

  it("enables only the edit operations that the owner provided", () => {
    const onSave = vi.fn();
    render(<EditMessage value="Updated request" discardedReplies={0} editing
      onSave={onSave} />);
    const editor = screen.getByRole("textbox", { name: "Edit your message" });
    expect(editor.hasAttribute("disabled")).toBe(true);
    expect(screen.getByRole("button", { name: "Cancel" }).hasAttribute("disabled")).toBe(true);
    const send = screen.getByRole("button", { name: "Send" });
    expect(send.hasAttribute("disabled")).toBe(false);
    fireEvent.click(send);
    expect(onSave).toHaveBeenCalledTimes(1);
  });
});

describe("MessageQueue with live queued and streaming producer values", () => {
  const queued = [{ id: "queued-a", text: "First queued prompt" }, { id: "queued-b", text: "Second queued prompt" }];

  it("shows queued work without inventing an active run while idle", () => {
    render(<MessageQueue queued={queued} />);
    expect(screen.getByText("2 queued")).toBeTruthy();
    expect(screen.getByText("Waiting to send")).toBeTruthy();
    expect(screen.getByText("First queued prompt")).toBeTruthy();
    expect(screen.getByText("Second queued prompt")).toBeTruthy();
    expect(screen.queryByText("running")).toBeNull();
    expect(screen.queryByRole("button", { name: "Stop current response" })).toBeNull();
    expect(screen.queryByRole("button", { name: /Send .* next/ })).toBeNull();
  });

  it("uses the caller's real queue status and changes it when streaming begins", () => {
    const { rerender } = render(<MessageQueue queued={queued} queueHint="Waiting for connection" />);
    expect(screen.getByText("Waiting for connection")).toBeTruthy();
    rerender(<MessageQueue running="Generating response" queued={queued}
      queueHint="Sends after the current response" />);
    expect(screen.getByText("Generating response")).toBeTruthy();
    expect(screen.getByText("running")).toBeTruthy();
    expect(screen.getByText("Sends after the current response")).toBeTruthy();
    expect(screen.queryByText("Waiting for connection")).toBeNull();
  });

  it("routes per-item send-next, edit and cancel to each stable queue ID", () => {
    const onInterruptQueued = vi.fn();
    const onEdit = vi.fn();
    const onCancel = vi.fn();
    const { container } = render(<MessageQueue running="Generating response" queued={queued}
      onInterruptQueued={onInterruptQueued} onEdit={onEdit} onCancel={onCancel} />);
    const rows = container.querySelectorAll('[data-slot="message-queue"] li');
    expect(rows).toHaveLength(2);
    fireEvent.click(within(rows[1] as HTMLElement).getByRole("button", { name: 'Send "Second queued prompt" next' }));
    fireEvent.click(within(rows[0] as HTMLElement).getByRole("button", { name: 'Edit "First queued prompt" in the queue' }));
    fireEvent.click(within(rows[1] as HTMLElement).getByRole("button", { name: 'Remove "Second queued prompt" from the queue' }));
    expect(onInterruptQueued.mock.calls).toEqual([["queued-b"]]);
    expect(onEdit.mock.calls).toEqual([["queued-a"]]);
    expect(onCancel.mock.calls).toEqual([["queued-b"]]);
  });

  it("keeps stop-current separate from sending a queued item next", () => {
    const onInterrupt = vi.fn();
    const onInterruptQueued = vi.fn();
    render(<MessageQueue running="Current response" queued={queued}
      onInterrupt={onInterrupt} onInterruptQueued={onInterruptQueued} />);
    fireEvent.click(screen.getByRole("button", { name: "Stop current response" }));
    expect(onInterrupt).toHaveBeenCalledTimes(1);
    expect(onInterruptQueued).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: 'Send "First queued prompt" next' }));
    expect(onInterrupt).toHaveBeenCalledTimes(1);
    expect(onInterruptQueued).toHaveBeenCalledWith("queued-a");
  });

  it("uses caller labels for hosted queue counts, status and each real action", () => {
    const onInterrupt = vi.fn();
    const onInterruptQueued = vi.fn();
    const onEdit = vi.fn();
    const onCancel = vi.fn();
    const labels = {
      running: "läuft",
      queuedCount: (count: number) => `${count} in Warteschlange`,
      stopCurrent: "Antwort stoppen",
      sendNext: (text: string) => `${text} als Nächstes senden`,
      edit: (text: string) => `${text} bearbeiten`,
      remove: (text: string) => `${text} entfernen`,
    };
    render(<MessageQueue running="Antwort wird erstellt" queued={queued} labels={labels}
      queueHint="Wartet auf die Antwort" onInterrupt={onInterrupt}
      onInterruptQueued={onInterruptQueued} onEdit={onEdit} onCancel={onCancel} />);
    expect(screen.getByText("läuft")).toBeTruthy();
    expect(screen.getByText("2 in Warteschlange")).toBeTruthy();
    expect(screen.getByText("Wartet auf die Antwort")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Antwort stoppen" }));
    fireEvent.click(screen.getByRole("button", { name: "Second queued prompt als Nächstes senden" }));
    fireEvent.click(screen.getByRole("button", { name: "First queued prompt bearbeiten" }));
    fireEvent.click(screen.getByRole("button", { name: "Second queued prompt entfernen" }));
    expect(onInterrupt).toHaveBeenCalledTimes(1);
    expect(onInterruptQueued).toHaveBeenCalledWith("queued-b");
    expect(onEdit).toHaveBeenCalledWith("queued-a");
    expect(onCancel).toHaveBeenCalledWith("queued-b");
  });
});

describe("MessageBranches with real variant counts and no invented bodies", () => {
  it("shows control-only count and current index without rendering a response body", () => {
    const onIndexChange = vi.fn();
    const { container } = render(<MessageBranches count={3} index={1} showBody={false}
      onIndexChange={onIndexChange} />);
    expect(screen.getByText("2 / 3")).toBeTruthy();
    expect(container.querySelector('[data-slot="message-branches"] p')).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Show previous response" }));
    fireEvent.click(screen.getByRole("button", { name: "Show next response" }));
    expect(onIndexChange.mock.calls).toEqual([[0], [2]]);
  });

  it("wraps count-only navigation using recorded endpoints", () => {
    const onIndexChange = vi.fn();
    const { rerender } = render(<MessageBranches count={3} index={0} showBody={false}
      onIndexChange={onIndexChange} />);
    fireEvent.click(screen.getByRole("button", { name: "Show previous response" }));
    expect(onIndexChange).toHaveBeenCalledWith(2);
    rerender(<MessageBranches count={3} index={2} showBody={false} onIndexChange={onIndexChange} />);
    fireEvent.click(screen.getByRole("button", { name: "Show next response" }));
    expect(onIndexChange).toHaveBeenCalledWith(0);
    expect(screen.getByText("3 / 3")).toBeTruthy();
  });

  it("disables switching without a real latest-turn callback", () => {
    const { container, rerender } = render(<MessageBranches count={2} index={0} showBody={false} />);
    expect(screen.getByText("1 / 2")).toBeTruthy();
    for (const button of screen.getAllByRole("button")) expect(button.hasAttribute("disabled")).toBe(true);
    expect(container.querySelector('[data-slot="message-branches"] p')).toBeNull();
    rerender(<MessageBranches count={0} index={0} showBody={false} onIndexChange={vi.fn()} />);
    expect(screen.getByText("0 / 0")).toBeTruthy();
    for (const button of screen.getAllByRole("button")) expect(button.hasAttribute("disabled")).toBe(true);
  });

  it("never inserts a fallback body when only a count was supplied", () => {
    const { container } = render(<MessageBranches count={2} index={0} onIndexChange={vi.fn()} />);
    expect(screen.getByText("1 / 2")).toBeTruthy();
    expect(container.querySelector('[data-slot="message-branches"] p')).toBeNull();
  });

  it("shows no variant count or body before the owner provides either source", () => {
    const { container } = render(<MessageBranches index={0} />);
    expect(screen.getByText("0 / 0")).toBeTruthy();
    expect(container.querySelector('[data-slot="message-branches"] p')).toBeNull();
    for (const button of screen.getAllByRole("button")) expect(button.hasAttribute("disabled")).toBe(true);
  });

  it("uses caller navigation labels without changing the recorded variant index", () => {
    const onIndexChange = vi.fn();
    render(<MessageBranches count={2} index={0} showBody={false} onIndexChange={onIndexChange}
      labels={{ previous: "Vorherige Antwort", next: "Nächste Antwort" }} />);
    fireEvent.click(screen.getByRole("button", { name: "Vorherige Antwort" }));
    fireEvent.click(screen.getByRole("button", { name: "Nächste Antwort" }));
    expect(onIndexChange.mock.calls).toEqual([[1], [1]]);
    expect(screen.getByText("1 / 2")).toBeTruthy();
  });
});

describe("MessageActions with persisted feedback and caller-owned controls", () => {
  it("shows only live callbacks and preserves fork/speak children", () => {
    const fork = vi.fn();
    render(<MessageActions><button type="button" onClick={fork}>Fork conversation</button></MessageActions>);
    expect(screen.getAllByRole("button")).toHaveLength(1);
    expect(screen.queryByRole("button", { name: "Copy response" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Mark response helpful" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Regenerate response" })).toBeNull();
    expect(screen.queryByRole("button", { name: "More response actions" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Fork conversation" }));
    expect(fork).toHaveBeenCalledTimes(1);
  });

  it("keeps persisted helpful feedback pressed without offering an unsupported unvote", () => {
    const onReactionChange = vi.fn();
    render(<MessageActions reaction="up" allowClearReaction={false}
      onReactionChange={onReactionChange} />);
    const helpful = screen.getByRole("button", { name: "Mark response helpful" });
    const unhelpful = screen.getByRole("button", { name: "Mark response unhelpful" });
    expect(helpful.getAttribute("aria-pressed")).toBe("true");
    expect(helpful.hasAttribute("disabled")).toBe(true);
    expect(unhelpful.hasAttribute("disabled")).toBe(false);
    fireEvent.click(helpful);
    expect(onReactionChange).not.toHaveBeenCalled();
    fireEvent.click(unhelpful);
    expect(onReactionChange.mock.calls).toEqual([["down"]]);
  });

  it("keeps persisted unhelpful feedback pressed but allows a real replacement", () => {
    const onReactionChange = vi.fn();
    render(<MessageActions reaction="down" allowClearReaction={false}
      onReactionChange={onReactionChange} />);
    const unhelpful = screen.getByRole("button", { name: "Mark response unhelpful" });
    expect(unhelpful.getAttribute("aria-pressed")).toBe("true");
    expect(unhelpful.hasAttribute("disabled")).toBe(true);
    fireEvent.click(screen.getByRole("button", { name: "Mark response helpful" }));
    expect(onReactionChange.mock.calls).toEqual([["up"]]);
  });

  it("blocks both verdict controls during the real feedback request", () => {
    const onReactionChange = vi.fn();
    render(<MessageActions reaction="up" reactionBusy allowClearReaction={false}
      onReactionChange={onReactionChange} />);
    for (const button of screen.getAllByRole("button")) {
      expect(button.hasAttribute("disabled")).toBe(true);
      expect(button.getAttribute("aria-busy")).toBe("true");
      fireEvent.click(button);
    }
    expect(onReactionChange).not.toHaveBeenCalled();
  });

  it("shows independently supplied copy/regenerate controls without invented More", () => {
    const onCopy = vi.fn();
    const onRegenerate = vi.fn();
    render(<MessageActions onCopy={onCopy} onRegenerate={onRegenerate} />);
    expect(screen.queryByRole("button", { name: "Mark response helpful" })).toBeNull();
    expect(screen.queryByRole("button", { name: "More response actions" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Copy response" }));
    fireEvent.click(screen.getByRole("button", { name: "Regenerate response" }));
    expect(onCopy).toHaveBeenCalledTimes(1);
    expect(onRegenerate).toHaveBeenCalledTimes(1);
  });

  it("uses caller action labels while retaining each real callback and copied state", () => {
    const onCopy = vi.fn();
    const onReactionChange = vi.fn();
    const onRegenerate = vi.fn();
    const onMore = vi.fn();
    const labels = { copy: "Kopieren", copied: "Kopiert", helpful: "Hilfreich",
      unhelpful: "Nicht hilfreich", regenerate: "Erneuern", more: "Weitere Aktionen" };
    const { rerender } = render(<MessageActions labels={labels} onCopy={onCopy}
      onReactionChange={onReactionChange} onRegenerate={onRegenerate} onMore={onMore} />);
    fireEvent.click(screen.getByRole("button", { name: "Kopieren" }));
    fireEvent.click(screen.getByRole("button", { name: "Hilfreich" }));
    fireEvent.click(screen.getByRole("button", { name: "Nicht hilfreich" }));
    fireEvent.click(screen.getByRole("button", { name: "Erneuern" }));
    fireEvent.click(screen.getByRole("button", { name: "Weitere Aktionen" }));
    expect(onCopy).toHaveBeenCalledTimes(1);
    expect(onReactionChange.mock.calls).toEqual([["up"], ["down"]]);
    expect(onRegenerate).toHaveBeenCalledTimes(1);
    expect(onMore).toHaveBeenCalledTimes(1);
    rerender(<MessageActions copied labels={labels} onCopy={onCopy} />);
    expect(screen.getByRole("button", { name: "Kopiert" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Kopieren" })).toBeNull();
  });
});

describe("EditMessage hosted labels while retaining donor defaults", () => {
  it("uses supplied visible labels and reports the true discarded count", () => {
    const onValueChange = vi.fn();
    const onSave = vi.fn();
    const onCancel = vi.fn();
    const discardedReplies = vi.fn((count: number) => `Replaces ${count} later replies`);
    render(<EditMessage value="Original prompt" discardedReplies={2} editing
      onValueChange={onValueChange} onSave={onSave} onCancel={onCancel}
      labels={{ edit: "Nachricht bearbeiten", cancel: "Abbrechen", send: "Erneut senden", discardedReplies }} />);
    const editor = screen.getByRole("textbox", { name: "Nachricht bearbeiten" });
    fireEvent.change(editor, { target: { value: "Revised prompt" } });
    expect(onValueChange).toHaveBeenCalledWith("Revised prompt");
    expect(screen.getByText("Replaces 2 later replies")).toBeTruthy();
    expect(discardedReplies).toHaveBeenCalledWith(2);
    fireEvent.click(screen.getByRole("button", { name: "Erneut senden" }));
    fireEvent.click(screen.getByRole("button", { name: "Abbrechen" }));
    expect(onSave).toHaveBeenCalledTimes(1);
    expect(onCancel).toHaveBeenCalledTimes(1);
  });

  it("keeps caller labels optional and never calls a discard formatter for zero replies", () => {
    const discardedReplies = vi.fn((count: number) => `${count} replies`);
    render(<EditMessage value="Edited request" discardedReplies={0} editing
      onSave={vi.fn()} labels={{ send: "Resend", discardedReplies }} />);
    expect(screen.getByRole("textbox", { name: "Edit your message" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Cancel" }).hasAttribute("disabled")).toBe(true);
    expect(screen.getByRole("button", { name: "Resend" })).toBeTruthy();
    expect(discardedReplies).not.toHaveBeenCalled();
  });
});
