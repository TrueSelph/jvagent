/**
 * Agent-driven UI components ("static generative UI").
 *
 * The **frontend owns the catalog** — the agent only names a component and
 * supplies data via `metadata.ui`. That keeps rendering consistent and safe: a
 * model can never inject markup or layout, only fill in a shape we defined.
 *
 * Every renderer degrades to the envelope's `fallback` text rather than
 * throwing or rendering blank, so an unknown component, a version bump, or a
 * malformed payload always leaves something readable in the thread.
 */

import type { FC } from "react";
import type { ToolCallMessagePartProps } from "@assistant-ui/react";
import { ExternalLinkIcon } from "lucide-react";
import type { UiEnvelope } from "../streaming/types";

/** Only these schemes may appear in an agent-supplied link. */
function safeHref(raw: unknown): string | null {
  if (typeof raw !== "string") return null;
  try {
    const url = new URL(raw, window.location.href);
    return ["https:", "mailto:", "tel:"].includes(url.protocol) ? url.href : null;
  } catch {
    return null;
  }
}

function asString(v: unknown): string {
  return typeof v === "string" ? v.trim() : "";
}

function asArray(v: unknown): Record<string, unknown>[] {
  return Array.isArray(v)
    ? v.filter((x): x is Record<string, unknown> => !!x && typeof x === "object")
    : [];
}

const Fallback: FC<{ text?: string }> = ({ text }) =>
  text ? <p className="text-muted-foreground my-1 text-sm">{text}</p> : null;

/** Read the envelope off a tool-call part's args. */
function envelopeOf(props: ToolCallMessagePartProps): UiEnvelope | null {
  const args = props.args as unknown as UiEnvelope | undefined;
  return args && typeof args === "object" && args.component ? args : null;
}

// ── card ───────────────────────────────────────────────────────────────────

const Card: FC<{ env: UiEnvelope; onSend: (text: string) => void }> = ({
  env,
  onSend,
}) => {
  const p = env.props ?? {};
  const title = asString(p.title);
  const subtitle = asString(p.subtitle);
  const body = asString(p.body);
  const fields = asArray(p.fields);
  const actions = asArray(p.actions);
  const image = (p.image ?? {}) as Record<string, unknown>;
  const imageUrl = safeHref(image.url);

  if (!title && !body && !imageUrl) return <Fallback text={env.fallback} />;

  return (
    <div className="border-border bg-background animate-in fade-in slide-in-from-bottom-1 my-2 overflow-hidden rounded-xl border">
      {imageUrl && (
        <img
          src={imageUrl}
          alt={asString(image.alt)}
          className="max-h-40 w-full object-cover"
        />
      )}
      <div className="flex flex-col gap-1 p-3">
        {title && <span className="text-sm font-semibold">{title}</span>}
        {subtitle && (
          <span className="text-muted-foreground text-xs">{subtitle}</span>
        )}
        {body && <p className="mt-1 text-sm leading-relaxed">{body}</p>}

        {fields.length > 0 && (
          <dl className="mt-2 grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-xs">
            {fields.map((f, i) => (
              <div key={i} className="contents">
                <dt className="text-muted-foreground">{asString(f.label)}</dt>
                <dd className="truncate">{asString(f.value)}</dd>
              </div>
            ))}
          </dl>
        )}

        {actions.length > 0 && (
          <div className="mt-2 flex flex-wrap gap-1.5">
            {actions.map((a, i) => {
              const label = asString(a.label);
              if (!label) return null;
              const href = a.kind === "link" ? safeHref(a.href) : null;
              if (a.kind === "link") {
                return href ? (
                  <a
                    key={i}
                    href={href}
                    target="_blank"
                    rel="noreferrer noopener"
                    className="border-border hover:bg-muted inline-flex items-center gap-1 rounded-full border px-3 py-1.5 text-xs transition-colors"
                  >
                    {label}
                    <ExternalLinkIcon className="size-3" />
                  </a>
                ) : null;
              }
              return (
                <button
                  key={i}
                  type="button"
                  onClick={() => onSend(asString(a.value) || label)}
                  className="border-border hover:bg-muted rounded-full border px-3 py-1.5 text-xs transition-colors"
                >
                  {label}
                </button>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
};

// ── choices ────────────────────────────────────────────────────────────────

const Choices: FC<{ env: UiEnvelope; onSend: (text: string) => void }> = ({
  env,
  onSend,
}) => {
  const p = env.props ?? {};
  const prompt = asString(p.prompt);
  const options = asArray(p.options).filter((o) => asString(o.label));
  if (!options.length) return <Fallback text={env.fallback} />;

  return (
    <div className="animate-in fade-in slide-in-from-bottom-1 my-2 flex flex-col gap-1.5">
      {prompt && <span className="text-muted-foreground text-xs">{prompt}</span>}
      <div className="flex flex-wrap gap-1.5">
        {options.map((o, i) => {
          const label = asString(o.label);
          const disabled = o.disabled === true;
          return (
            <button
              key={i}
              type="button"
              disabled={disabled}
              // A tap sends the label verbatim — same contract as the chips.
              onClick={() => onSend(asString(o.value) || label)}
              className="border-border hover:bg-muted flex flex-col items-start rounded-xl border px-3 py-2 text-left text-xs transition-colors disabled:cursor-not-allowed disabled:opacity-50"
            >
              <span className="font-medium">{label}</span>
              {asString(o.description) && (
                <span className="text-muted-foreground">
                  {asString(o.description)}
                </span>
              )}
            </button>
          );
        })}
      </div>
    </div>
  );
};

/**
 * Build the `tools.by_name` map for `MessagePrimitive.Parts`. Components are
 * addressed as `ui_<component>`; anything unregistered hits the Fallback and
 * renders its plain-text form.
 */
export function uiPartComponents(onSend: (text: string) => void) {
  const wrap =
    (Component: FC<{ env: UiEnvelope; onSend: (text: string) => void }>) =>
    (props: ToolCallMessagePartProps) => {
      const env = envelopeOf(props);
      if (!env) return null;
      return <Component env={env} onSend={onSend} />;
    };

  return {
    by_name: {
      ui_card: wrap(Card),
      ui_choices: wrap(Choices),
    },
    // Unknown component or a future envelope version: show the agent's own
    // plain-text rendering rather than nothing.
    Fallback: (props: ToolCallMessagePartProps) => {
      const env = envelopeOf(props);
      return <Fallback text={env?.fallback} />;
    },
  };
}
