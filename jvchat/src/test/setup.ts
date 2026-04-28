import "@testing-library/jest-dom/vitest";

if (!Element.prototype.scrollIntoView) {
  Element.prototype.scrollIntoView = () => {};
}

/** Vitest/Node: previews need blob URLs in tests without full DOM. */
if (typeof URL.createObjectURL !== "function") {
  (URL as unknown as { createObjectURL: (b: Blob) => string }).createObjectURL =
    () => "blob:mock";
}
if (typeof URL.revokeObjectURL !== "function") {
  (URL as unknown as { revokeObjectURL: (s: string) => void }).revokeObjectURL =
    () => {};
}
