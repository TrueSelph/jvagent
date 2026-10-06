import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const mockState = vi.hoisted(() => ({ value: {} as Record<string, unknown> }));

vi.mock("@assistant-ui/react", () => ({
  useAuiState: (selector: (state: typeof mockState.value) => unknown) =>
    selector(mockState.value),
}));

import { MessageStats } from "./message-stats";

function setInteraction(usage: Record<string, unknown>, metrics: unknown[]) {
  mockState.value = {
    message: {
      role: "assistant",
      status: { type: "complete" },
      metadata: {
        custom: {
          debugMessage: {
            debugData: {
              interaction: { usage, observability_metrics: metrics },
            },
          },
        },
      },
    },
  };
}

describe("MessageStats", () => {
  beforeEach(() => {
    mockState.value = {};
  });

  it("surfaces failed attempts and unknown provider cost without implying free usage", () => {
    setInteraction(
      {
        model_attempt_count: 1,
        failed_model_attempt_count: 1,
        model_attempt_duration_seconds: 0.253,
        model_call_count: 0,
        total_cost_usd: 0,
        unknown_cost_call_count: 1,
      },
      [
        {
          event_type: "model_attempt",
          data: {
            provider: "ollama",
            model: "glm-5.3:cloud",
            outcome: "failed",
            duration: 0.253,
          },
        },
      ],
    );

    render(<MessageStats />);

    expect(screen.getByRole("button")).toHaveTextContent(
      "glm-5.3:cloud · 1 attempt · 253ms provider time · 1 unknown-cost event",
    );
    fireEvent.click(screen.getByRole("button"));
    expect(screen.getByRole("status")).toHaveTextContent(
      "1 provider attempt · 1 failed · Cost unknown for 1 provider attempt; check provider billing.",
    );
    expect(screen.queryByText("$0.0000")).not.toBeInTheDocument();
  });

  it("keeps a reported zero-cost successful call distinguishable from unknown cost", () => {
    setInteraction(
      { total_cost_usd: 0, unknown_cost_call_count: 0, total_tokens: 10 },
      [
        {
          event_type: "model_call",
          data: { model: "gpt-4.1-mini", usage: { prompt_tokens: 10 } },
        },
      ],
    );

    render(<MessageStats />);

    expect(screen.getByRole("button")).toHaveTextContent("gpt-4.1-mini");
    fireEvent.click(screen.getByRole("button"));
    expect(screen.queryByText(/Cost unknown/)).not.toBeInTheDocument();
  });
});
