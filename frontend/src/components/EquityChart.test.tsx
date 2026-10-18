import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { EquityChart } from "./EquityChart";

describe("EquityChart", () => {
  it("draws a line and a drawdown band", () => {
    const pts = [100, 110, 99, 120].map((equity, i) => ({ ts: String(i), equity, drawdown: [0, 0, -0.1, 0][i] }));
    const { container } = render(<EquityChart points={pts} />);
    expect(container.querySelector("path.eq.up")).not.toBeNull();
    expect(container.querySelector("path.dd")).not.toBeNull();
    expect(container.querySelector("svg")?.getAttribute("aria-label")).toBe("Equity from 100 to 120");
  });

  it("says so when there is too little data", () => {
    const { getByText } = render(<EquityChart points={[{ ts: "0", equity: 1 }]} />);
    expect(getByText(/Not enough history/)).toBeTruthy();
  });
});
