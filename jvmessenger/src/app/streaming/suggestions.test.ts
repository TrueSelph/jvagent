import { describe, it, expect } from "vitest";
import { extractSuggestions } from "./types";

describe("extractSuggestions", () => {
  it("maps string suggestions to {label,value}", () => {
    expect(extractSuggestions({ suggestions: ["Yes", "No"] })).toEqual([
      { label: "Yes", value: "Yes" },
      { label: "No", value: "No" },
    ]);
  });

  it("maps action objects with distinct label/value", () => {
    expect(
      extractSuggestions({ actions: [{ label: "Refund", value: "refund_flow" }] })
    ).toEqual([{ label: "Refund", value: "refund_flow" }]);
  });

  it("falls back value→label when value missing", () => {
    expect(extractSuggestions({ actions: [{ label: "Help" }] })).toEqual([
      { label: "Help", value: "Help" },
    ]);
  });

  it("ignores empties / non-arrays / missing metadata", () => {
    expect(extractSuggestions(undefined)).toEqual([]);
    expect(extractSuggestions({})).toEqual([]);
    expect(extractSuggestions({ suggestions: "nope" })).toEqual([]);
    expect(extractSuggestions({ suggestions: ["", "  "] })).toEqual([]);
  });
});
