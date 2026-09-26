import { afterEach, describe, expect, it } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { HeatGraph } from "./heat-graph";

afterEach(cleanup);

function calendarDate(daysAgo = 0) {
  const date = new Date();
  date.setDate(date.getDate() - daysAgo);
  return new Date(date.getFullYear(), date.getMonth(), date.getDate());
}

function graphCells(container: HTMLElement) {
  return Array.from(container.querySelectorAll<HTMLDivElement>(".aspect-square"));
}

describe("donor HeatGraph with real calendar data", () => {
  it("renders a Monday-first year grid with matching day and month labels", () => {
    const { container } = render(<HeatGraph data={[]} />);
    const root = container.firstElementChild;
    expect(root).toHaveClass("flex", "flex-col", "gap-2");
    const grid = container.querySelector<HTMLElement>("[style*=\"grid-template-rows\"]");
    expect(grid).not.toBeNull();
    expect(grid).toHaveStyle({ display: "grid" });
    expect(grid).toHaveStyle({ gridTemplateRows: "repeat(7, 1fr)" });
    expect(graphCells(container).length).toBeGreaterThanOrEqual(365);
    expect(graphCells(container).length).toBeLessThanOrEqual(371);
    const labels = container.querySelectorAll(".w-8 span");
    expect(labels).toHaveLength(7);
    expect(labels[0]).toHaveTextContent("Mon");
    expect(labels[1]).toBeEmptyDOMElement();
    expect(labels[2]).toHaveTextContent("Wed");
    expect(labels[3]).toBeEmptyDOMElement();
    expect(labels[4]).toHaveTextContent("Fri");
    expect(labels[5]).toBeEmptyDOMElement();
    expect(labels[6]).toHaveTextContent("Sun");
    expect(container.querySelector(".relative.ms-10 span")).not.toBeNull();
  });

  it("renders the donor legend and neutral color for days without activity", () => {
    const { container } = render(<HeatGraph data={[]} />);
    expect(screen.getByText("Less")).toBeInTheDocument();
    expect(screen.getByText("More")).toBeInTheDocument();
    const cells = graphCells(container);
    expect(cells.length).toBeGreaterThan(364);
    expect(cells[0]).toHaveStyle({ backgroundColor: "#ebedf0" });
    expect(cells[cells.length - 1]).toHaveStyle({ backgroundColor: "#ebedf0" });
    const legend = screen.getByText("Less").parentElement;
    expect(legend).toHaveClass("ms-auto");
    expect(legend?.querySelector(".rounded-sm")).not.toBeNull();
  });

  it("colors actual contributions and exposes their count on hover", async () => {
    const today = calendarDate();
    const { container } = render(<HeatGraph data={[{ date: today, count: 4 }]} />);
    const cells = graphCells(container);
    const todayCell = cells[cells.length - 1];
    expect(todayCell).toHaveStyle({ backgroundColor: "#2563eb" });
    expect(cells[0]).toHaveStyle({ backgroundColor: "#ebedf0" });
    expect(screen.queryByText("4 contributions")).not.toBeInTheDocument();
    fireEvent.mouseEnter(todayCell);
    expect(await screen.findByText("4 contributions")).toBeInTheDocument();
    expect(screen.getByText("4 contributions").parentElement).toHaveTextContent(`on ${today.toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" })}`);
    fireEvent.mouseLeave(todayCell);
    expect(screen.queryByText("4 contributions")).not.toBeInTheDocument();
  });

  it("uses the caller unit for real activity without changing date or count", async () => {
    const today = calendarDate();
    const { container } = render(
      <HeatGraph data={[{ date: today, count: 3 }]} unitLabel="findings" />,
    );
    const cell = graphCells(container).at(-1)!;
    fireEvent.mouseEnter(cell);
    const count = await screen.findByText("3 findings");
    expect(count).toBeInTheDocument();
    expect(count.parentElement).toHaveTextContent(`on ${today.toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" })}`);
    expect(screen.queryByText("3 contributions")).not.toBeInTheDocument();
  });

  it("labels a real zero-count day with the caller unit", async () => {
    const { container } = render(<HeatGraph data={[]} unitLabel="findings" />);
    fireEvent.mouseEnter(graphCells(container).at(-1)!);
    expect(await screen.findByText("0 findings")).toBeInTheDocument();
    expect(screen.queryByText("0 contributions")).not.toBeInTheDocument();
  });

  it("aggregates same-day records through the real grid dependency", async () => {
    const today = calendarDate();
    const yesterday = calendarDate(1);
    const { container } = render(
      <HeatGraph data={[
        { date: today, count: 2 },
        { date: today, count: 3 },
        { date: yesterday, count: 1 },
      ]} />,
    );
    const cells = graphCells(container);
    fireEvent.mouseEnter(cells[cells.length - 1]);
    expect(await screen.findByText("5 contributions")).toBeInTheDocument();
    fireEvent.mouseLeave(cells[cells.length - 1]);
    fireEvent.mouseEnter(cells[cells.length - 2]);
    expect(await screen.findByText("1 contributions")).toBeInTheDocument();
    expect(screen.queryByText("5 contributions")).not.toBeInTheDocument();
  });

  it("updates the current day when caller data changes", async () => {
    const today = calendarDate();
    const { container, rerender } = render(
      <HeatGraph data={[{ date: today, count: 1 }]} />,
    );
    let cells = graphCells(container);
    expect(cells[cells.length - 1]).toHaveStyle({ backgroundColor: "#2563eb" });
    fireEvent.mouseEnter(cells[cells.length - 1]);
    expect(await screen.findByText("1 contributions")).toBeInTheDocument();
    fireEvent.mouseLeave(cells[cells.length - 1]);
    rerender(<HeatGraph data={[{ date: today, count: 7 }]} />);
    cells = graphCells(container);
    fireEvent.mouseEnter(cells[cells.length - 1]);
    expect(await screen.findByText("7 contributions")).toBeInTheDocument();
    expect(screen.queryByText("1 contributions")).not.toBeInTheDocument();
  });

  it("keeps month positions within the rendered calendar grid", () => {
    const { container } = render(<HeatGraph data={[]} />);
    const months = container.querySelectorAll<HTMLElement>(".relative.ms-10 span");
    expect(months.length).toBeGreaterThanOrEqual(12);
    expect(months.length).toBeLessThanOrEqual(13);
    for (const month of months) {
      const left = Number.parseFloat(month.style.left);
      expect(left).toBeGreaterThanOrEqual(0);
      expect(left).toBeLessThan(100);
      expect(month.textContent).toMatch(/^(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)$/);
    }
  });
});
