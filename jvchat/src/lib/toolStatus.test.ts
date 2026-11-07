import { describe, expect, it } from "vitest";
import { toolPresentation } from "./toolStatus";

describe("tool acknowledgement presentation", () => {
  it("never presents a finished turn's result-less call as successful", () => {
    const view = toolPresentation({ type: "complete" }, undefined);
    expect(view.unconfirmed).toBe(true);
    expect(view.status).toMatchObject({ type: "incomplete", reason: "error" });
    expect(view.status).toHaveProperty("error", expect.stringContaining("may have happened"));
  });
  it("handles missing status conservatively", () => {
    expect(toolPresentation(undefined, undefined).unconfirmed).toBe(true);
  });
  it("preserves a running call until its turn ends", () => {
    expect(toolPresentation({ type: "running" }, undefined)).toEqual({
      status: { type: "running" }, unconfirmed: false,
    });
  });
  it.each([null, "", false, 0, { ok: true }])("accepts an explicit result %j", (result) => {
    expect(toolPresentation({ type: "complete" }, result)).toEqual({
      status: { type: "complete" }, unconfirmed: false,
    });
  });
  it("uses the structured failure flag even when the content looks successful", () => {
    expect(toolPresentation({ type: "complete" }, "success", true).status).toMatchObject({
      type: "incomplete", reason: "error",
    });
  });
  it("preserves cancellation and human-action states", () => {
    const cancelled = { type: "incomplete", reason: "cancelled" } as const;
    const approval = { type: "requires-action", reason: "interrupt" } as const;
    expect(toolPresentation(cancelled, undefined).status).toBe(cancelled);
    expect(toolPresentation(approval, undefined).status).toBe(approval);
  });
});
