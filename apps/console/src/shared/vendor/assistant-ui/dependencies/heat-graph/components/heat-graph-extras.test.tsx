import { afterEach, describe, expect, it } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import {
  Cell,
  Grid,
  Legend,
  LegendLevel,
  MonthLabels,
  MONTH_SHORT,
  Root,
  Tooltip,
} from "../index";

afterEach(cleanup);

const colors = ["#eeeeee", "#c6d7f9", "#8fb0f3", "#5888e8", "#2563eb"];

function localDay(date: Date) {
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}`;
}

function FixedGraph({ count = 4 }: { count?: number }) {
  return (
    <Root
      data={[{ date: "2026-02-10", count }]}
      start="2026-01-01"
      end="2026-02-10"
      weekStart="monday"
      colorScale={colors}
      data-testid="fixed-root"
    >
      <div data-testid="month-labels">
        <MonthLabels>
          {({ label, totalWeeks }) => (
            <span data-testid="month">{MONTH_SHORT[label.month]}:{label.column}/{totalWeeks}</span>
          )}
        </MonthLabels>
      </div>
      <Grid data-testid="fixed-grid">
        {({ cell }) => (
          <Cell data-testid="day-cell" data-date={localDay(cell.date)} />
        )}
      </Grid>
      <div data-testid="legend">
        <Legend>
          {({ item }) => (
            <div data-testid="legend-entry">
              <span>{item.level}</span>
              <LegendLevel data-testid="legend-color" />
            </div>
          )}
        </Legend>
      </div>
      <Tooltip data-testid="cell-tooltip">
        {({ cell }) => <span>{cell.count} on {localDay(cell.date)}</span>}
      </Tooltip>
    </Root>
  );
}

describe("donor MonthLabels and local exports", () => {
  it("exports month names from the real dependency", () => {
    expect(MONTH_SHORT).toHaveLength(12);
    expect(MONTH_SHORT[0]).toBe("Jan");
    expect(MONTH_SHORT[1]).toBe("Feb");
    expect(MONTH_SHORT[11]).toBe("Dec");
  });

  it("uses context month columns and total weeks from a fixed calendar range", () => {
    render(<FixedGraph />);
    const labels = screen.getAllByTestId("month");
    expect(labels).toHaveLength(3);
    expect(labels[0]).toHaveTextContent("Dec:0/7");
    expect(labels[1]).toHaveTextContent("Jan:1/7");
    expect(labels[2]).toHaveTextContent("Feb:5/7");
    expect(screen.getByTestId("month-labels")).toContainElement(labels[0]);
    expect(screen.getByTestId("month-labels")).toContainElement(labels[1]);
  });

  it("does not invent month labels for an empty interval", () => {
    render(
      <Root data={[]} start="2026-02-02" end="2026-02-01" weekStart="monday">
        <MonthLabels>
          {({ label }) => <span>{MONTH_SHORT[label.month]}</span>}
        </MonthLabels>
      </Root>,
    );
    expect(screen.queryByText("Feb")).not.toBeInTheDocument();
    expect(screen.queryByText("Jan")).not.toBeInTheDocument();
  });
});

describe("donor Legend and LegendLevel", () => {
  it("renders the actual classified levels and each configured color", () => {
    render(<FixedGraph />);
    const entries = screen.getAllByTestId("legend-entry");
    const swatches = screen.getAllByTestId("legend-color");
    expect(entries).toHaveLength(5);
    expect(swatches).toHaveLength(5);
    expect(entries.map((entry) => entry.textContent)).toEqual(["0", "1", "2", "3", "4"]);
    expect(swatches[0]).toHaveStyle({ backgroundColor: colors[0] });
    expect(swatches[1]).toHaveStyle({ backgroundColor: colors[1] });
    expect(swatches[2]).toHaveStyle({ backgroundColor: colors[2] });
    expect(swatches[3]).toHaveStyle({ backgroundColor: colors[3] });
    expect(swatches[4]).toHaveStyle({ backgroundColor: colors[4] });
  });

  it("renders only a neutral level when every count is zero", () => {
    render(<FixedGraph count={0} />);
    expect(screen.getAllByTestId("legend-entry")).toHaveLength(1);
    expect(screen.getByTestId("legend-entry")).toHaveTextContent("0");
    expect(screen.getByTestId("legend-color")).toHaveStyle({ backgroundColor: colors[0] });
  });

  it("allows callers to size a swatch while retaining the context color", () => {
    render(
      <Root data={[]} start="2026-01-01" end="2026-01-07"
        weekStart="monday" colorScale={colors}>
        <Legend>
          {() => <LegendLevel data-testid="sized-swatch"
            style={{ width: "14px", height: "14px" }} />}
        </Legend>
      </Root>,
    );
    const swatch = screen.getByTestId("sized-swatch");
    expect(swatch).toHaveStyle({ backgroundColor: colors[0] });
    expect(swatch).toHaveStyle({ width: "14px", height: "14px" });
  });
});

describe("donor Tooltip with real cell hover dispatch", () => {
  it("is absent without a hovered cell", () => {
    render(<FixedGraph />);
    expect(screen.queryByTestId("cell-tooltip")).not.toBeInTheDocument();
    expect(screen.queryByText("4 on 2026-02-10")).not.toBeInTheDocument();
  });

  it("shows the hovered day and its real count", async () => {
    render(<FixedGraph />);
    const day = screen.getAllByTestId("day-cell").find(
      (cell) => cell.getAttribute("data-date") === "2026-02-10",
    );
    expect(day).toBeDefined();
    fireEvent.mouseEnter(day!);
    expect(await screen.findByText("4 on 2026-02-10")).toBeInTheDocument();
    expect(screen.getByTestId("cell-tooltip")).toBeInTheDocument();
    fireEvent.mouseLeave(day!);
    expect(screen.queryByText("4 on 2026-02-10")).not.toBeInTheDocument();
  });

  it("updates tooltip content when the hovered data point changes", async () => {
    const { rerender } = render(<FixedGraph count={2} />);
    let day = screen.getAllByTestId("day-cell").find(
      (cell) => cell.getAttribute("data-date") === "2026-02-10",
    )!;
    fireEvent.mouseEnter(day);
    expect(await screen.findByText("2 on 2026-02-10")).toBeInTheDocument();
    fireEvent.mouseLeave(day);
    rerender(<FixedGraph count={7} />);
    day = screen.getAllByTestId("day-cell").find(
      (cell) => cell.getAttribute("data-date") === "2026-02-10",
    )!;
    fireEvent.mouseEnter(day);
    expect(await screen.findByText("7 on 2026-02-10")).toBeInTheDocument();
    expect(screen.queryByText("2 on 2026-02-10")).not.toBeInTheDocument();
  });
});
