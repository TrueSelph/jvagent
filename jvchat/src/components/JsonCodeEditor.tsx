import CodeMirror from "@uiw/react-codemirror";
import { json } from "@codemirror/lang-json";
import { EditorView } from "@codemirror/view";
import { githubLight } from "@uiw/codemirror-theme-github";
import { vscodeDark } from "@uiw/codemirror-theme-vscode";
import { cn } from "../lib/utils";

const jsonExtensions = [json(), EditorView.lineWrapping];

export interface JsonCodeEditorProps {
  value: string;
  onChange?: (value: string) => void;
  /** Dark panel (matches app zinc dark surfaces). */
  dark?: boolean;
  className?: string;
  /** CodeMirror height (CSS length). Ignored when `fillHeight` is true. */
  height?: string;
  /**
   * Stretch to fill the parent flex region. Parent should use `flex flex-col` with a child
   * `flex-1 min-h-0` (pass via `className`). Sets CodeMirror to `height: 100%`.
   */
  fillHeight?: boolean;
  placeholder?: string;
  disabled?: boolean;
  readOnly?: boolean;
  /** Show line numbers, fold gutters, etc. Off for compact single-line fields. */
  basicSetup?: boolean;
}

export function JsonCodeEditor({
  value,
  onChange,
  dark = false,
  className,
  height = "min(320px, 50vh)",
  fillHeight = false,
  placeholder,
  disabled = false,
  readOnly = false,
  basicSetup = true,
}: JsonCodeEditorProps) {
  const editable = !disabled && !readOnly;
  const codemirrorHeight = fillHeight ? "100%" : height;

  return (
    <div
      className={cn(
        "json-code-editor overflow-hidden rounded-lg border text-sm leading-relaxed [&_.cm-editor]:outline-none [&_.cm-focused]:outline-none",
        dark ? "border-zinc-600" : "border-zinc-300",
        fillHeight && "flex min-h-0 flex-1 flex-col",
        className,
      )}
      style={fillHeight ? { minHeight: 0 } : undefined}
    >
      <CodeMirror
        value={value}
        height={codemirrorHeight}
        theme={dark ? vscodeDark : githubLight}
        extensions={jsonExtensions}
        onChange={editable && onChange ? onChange : undefined}
        editable={editable}
        placeholder={placeholder}
        basicSetup={basicSetup}
        className={cn(
          fillHeight && "min-h-0 flex-1 overflow-hidden [&_.cm-editor]:min-h-0",
        )}
      />
    </div>
  );
}
