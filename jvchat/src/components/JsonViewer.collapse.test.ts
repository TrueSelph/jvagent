import { describe, expect, it } from "vitest";
import { computeInitiallyExpanded } from "./jsonViewerCollapse";

describe("computeInitiallyExpanded", () => {
  const collapsed = new Set(["metadata"]);
  const gate = 2;

  it("collapses listed path when forcedDepth matches gate (reset)", () => {
    expect(
      computeInitiallyExpanded(1, 2, "metadata", collapsed, gate),
    ).toBe(false);
  });

  it("does not collapse listed path when forcedDepth exceeds gate (expand all)", () => {
    expect(
      computeInitiallyExpanded(1, 64, "metadata", collapsed, gate),
    ).toBe(true);
  });

  it("collapses nothing extra when gate depth beats forcedDepth", () => {
    expect(
      computeInitiallyExpanded(1, 0, "metadata", collapsed, gate),
    ).toBe(false);
  });

  it("does not collapse sibling keys", () => {
    expect(
      computeInitiallyExpanded(1, 2, "permissions", collapsed, gate),
    ).toBe(true);
  });

  it("root path empty stays governed only by depth vs forcedDepth", () => {
    expect(computeInitiallyExpanded(0, 2, "", collapsed, gate)).toBe(true);
    expect(computeInitiallyExpanded(0, 0, "", collapsed, gate)).toBe(false);
  });

  it("ignores collapsed paths when set is empty", () => {
    expect(
      computeInitiallyExpanded(1, 2, "metadata", new Set(), gate),
    ).toBe(true);
  });
});
