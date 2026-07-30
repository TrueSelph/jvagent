import { describe, expect, it } from "vitest";
import {
  extractUiComponents,
  isRenderableComponent,
  UI_ENVELOPE_VERSION,
} from "./types";
import { emptyTurn, reduceMessage } from "./reducer";
import type { ResponseMessageData } from "./types";

const env = (over = {}) => ({
  v: 1,
  component: "card",
  id: "ui_1",
  props: { title: "Order #1042" },
  fallback: "Order #1042 — shipped.",
  ...over,
});

describe("extractUiComponents", () => {
  it("accepts a single envelope", () => {
    expect(extractUiComponents({ ui: env() })).toHaveLength(1);
  });

  it("accepts an array and dedupes by id", () => {
    const out = extractUiComponents({ ui: [env(), env(), env({ id: "ui_2" })] });
    expect(out.map((e) => e.id)).toEqual(["ui_1", "ui_2"]);
  });

  it("drops malformed entries instead of throwing", () => {
    const out = extractUiComponents({
      ui: [null, "nope", {}, { component: "card" }, env()],
    });
    expect(out.map((e) => e.id)).toEqual(["ui_1"]);
  });

  it("returns empty when there is no ui key", () => {
    expect(extractUiComponents({ suggestions: ["a"] })).toEqual([]);
    expect(extractUiComponents(undefined)).toEqual([]);
  });

  it("defaults props and version", () => {
    const [e] = extractUiComponents({ ui: { component: "card", id: "x" } });
    expect(e.props).toEqual({});
    expect(e.v).toBe(1);
  });
});

describe("isRenderableComponent", () => {
  it("accepts known components at a supported version", () => {
    expect(isRenderableComponent(env() as never)).toBe(true);
    expect(isRenderableComponent(env({ component: "choices" }) as never)).toBe(true);
  });

  it("rejects unknown components so they fall back", () => {
    expect(isRenderableComponent(env({ component: "hologram" }) as never)).toBe(false);
  });

  it("rejects a future envelope version", () => {
    expect(
      isRenderableComponent(env({ v: UI_ENVELOPE_VERSION + 1 }) as never)
    ).toBe(false);
  });
});

describe("reducer collects components", () => {
  const msg = (over: Partial<ResponseMessageData>): ResponseMessageData => ({
    message_type: "adhoc",
    content: "",
    category: "user",
    ...over,
  });

  it("accumulates across messages without replacing", () => {
    let s = emptyTurn();
    s = reduceMessage(s, msg({ metadata: { ui: env() } }));
    s = reduceMessage(s, msg({ metadata: { ui: env({ id: "ui_2" }) } }));
    expect(s.ui.map((e) => e.id)).toEqual(["ui_1", "ui_2"]);
  });

  it("does not double-add the same component id", () => {
    let s = emptyTurn();
    s = reduceMessage(s, msg({ metadata: { ui: env() } }));
    s = reduceMessage(s, msg({ metadata: { ui: env() } }));
    expect(s.ui).toHaveLength(1);
  });

  it("keeps components and suggestions independent", () => {
    let s = emptyTurn();
    s = reduceMessage(s, msg({ metadata: { ui: env(), suggestions: ["A"] } }));
    expect(s.ui).toHaveLength(1);
    expect(s.suggestions.map((x) => x.label)).toEqual(["A"]);
  });
});
