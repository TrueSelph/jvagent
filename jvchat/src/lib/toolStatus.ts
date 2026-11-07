import type { ToolCallMessagePartStatus } from "@assistant-ui/react";

/** Presentation only: absent acknowledgement never proves success or rollback. */
export function toolPresentation(
  status: ToolCallMessagePartStatus | undefined,
  result: unknown,
  isError = false,
): { status: ToolCallMessagePartStatus; unconfirmed: boolean } {
  if (status?.type === "incomplete" || status?.type === "requires-action") {
    return { status, unconfirmed: false };
  }
  if (isError) {
    return {
      status: { type: "incomplete", reason: "error", error: "The tool reported a failure. See its result." },
      unconfirmed: false,
    };
  }
  if (status?.type !== "running" && result === undefined) {
    return {
      status: {
        type: "incomplete", reason: "error",
        error: "No confirmed result is available. The operation may have happened; check its outcome before retrying.",
      },
      unconfirmed: true,
    };
  }
  return { status: status ?? { type: "complete" }, unconfirmed: false };
}
