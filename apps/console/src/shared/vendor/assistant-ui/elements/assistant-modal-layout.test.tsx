import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { AssistantModal } from "./assistant-modal.aui";

describe("AssistantModal constrained content", () => {
  it("keeps a fixed header and scrollable history body in the controlled mobile dialog", () => {
    render(<AssistantModal open trigger={null}
      thread={<p>Current conversation</p>}
      history={<div><p>Saved session</p><button type="button">Quick reply</button></div>} />);

    const dialog = screen.getByRole("dialog", { name: "Assistant" });
    expect(dialog).toHaveClass("flex", "flex-col", "overflow-hidden");
    const header = dialog.querySelector('[data-slot="aui_assistant-modal-header"]');
    const body = dialog.querySelector('[data-slot="aui_assistant-modal-body"]');
    expect(header).toHaveClass("shrink-0");
    expect(body).toHaveClass("min-h-0", "flex-1", "overflow-auto");
    expect(within(dialog).getByText("Current conversation")).toBeInTheDocument();

    fireEvent.click(within(dialog).getByRole("button", { name: "History" }));
    expect(within(dialog).queryByText("Current conversation")).not.toBeInTheDocument();
    expect(within(body as HTMLElement).getByRole("button", { name: "Quick reply" })).toBeInTheDocument();
  });

});
