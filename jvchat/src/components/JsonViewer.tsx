import { useCallback, useMemo, useState } from "react";

type JsonValue =
  | string
  | number
  | boolean
  | null
  | undefined
  | JsonValue[]
  | { [key: string]: JsonValue };

interface JsonViewerProps {
  data: unknown;
  /** Levels expanded on first render. Defaults to 2. */
  defaultExpandDepth?: number;
  /** When true, renders inside a dark background. Defaults to true. */
  dark?: boolean;
  /** Show toolbar (expand/collapse all, raw toggle, search, copy). Defaults to true. */
  showToolbar?: boolean;
  /** Maximum container height (CSS value). Defaults to none. */
  maxHeight?: string;
  /** Optional className for the outer container. */
  className?: string;
}

type NodeKind = "object" | "array" | "string" | "number" | "boolean" | "null";

function kindOf(v: unknown): NodeKind {
  if (v === null || v === undefined) return "null";
  if (Array.isArray(v)) return "array";
  const t = typeof v;
  if (t === "object") return "object";
  if (t === "string") return "string";
  if (t === "number") return "number";
  if (t === "boolean") return "boolean";
  return "string";
}

function summary(value: unknown): string {
  if (Array.isArray(value)) return `Array(${value.length})`;
  if (value && typeof value === "object")
    return `Object {${Object.keys(value as object).length}}`;
  return "";
}

function valueLabel(value: unknown, kind: NodeKind, dark: boolean): JSX.Element {
  switch (kind) {
    case "string": {
      const s = value as string;
      return (
        <span
          className={dark ? "text-emerald-300" : "text-emerald-700"}
          title={s.length > 200 ? `${s.length} characters` : undefined}
        >
          "{s}"
        </span>
      );
    }
    case "number":
      return (
        <span className={dark ? "text-amber-300" : "text-amber-700"}>
          {String(value)}
        </span>
      );
    case "boolean":
      return (
        <span className={dark ? "text-purple-300" : "text-purple-700"}>
          {String(value)}
        </span>
      );
    case "null":
      return (
        <span className={dark ? "text-slate-400 italic" : "text-slate-500 italic"}>
          {value === undefined ? "undefined" : "null"}
        </span>
      );
    default:
      return <span>{String(value)}</span>;
  }
}

function tryDecodeEmbeddedJson(value: unknown): unknown | null {
  if (typeof value !== "string") return null;
  const s = value.trim();
  if (s.length < 2) return null;
  const first = s[0];
  const last = s[s.length - 1];
  if (!((first === "{" && last === "}") || (first === "[" && last === "]")))
    return null;
  try {
    return JSON.parse(s);
  } catch {
    return null;
  }
}

interface NodeProps {
  name?: string | number;
  value: unknown;
  depth: number;
  path: string;
  defaultExpandDepth: number;
  dark: boolean;
  search: string;
  isLast: boolean;
}

function highlight(text: string, query: string, dark: boolean): JSX.Element {
  if (!query) return <>{text}</>;
  const idx = text.toLowerCase().indexOf(query.toLowerCase());
  if (idx < 0) return <>{text}</>;
  const before = text.slice(0, idx);
  const match = text.slice(idx, idx + query.length);
  const after = text.slice(idx + query.length);
  return (
    <>
      {before}
      <mark
        className={
          dark
            ? "bg-yellow-500/40 text-yellow-100 rounded px-0.5"
            : "bg-yellow-200 text-yellow-900 rounded px-0.5"
        }
      >
        {match}
      </mark>
      {after}
    </>
  );
}

function nodeMatchesSearch(value: unknown, query: string): boolean {
  if (!query) return true;
  const q = query.toLowerCase();
  const visit = (v: unknown): boolean => {
    if (v === null || v === undefined) return "null".includes(q);
    if (Array.isArray(v)) return v.some(visit);
    if (typeof v === "object") {
      for (const [k, vv] of Object.entries(v as Record<string, unknown>)) {
        if (k.toLowerCase().includes(q)) return true;
        if (visit(vv)) return true;
      }
      return false;
    }
    return String(v).toLowerCase().includes(q);
  };
  return visit(value);
}

function Node({
  name,
  value,
  depth,
  path,
  defaultExpandDepth,
  dark,
  search,
  isLast,
}: NodeProps) {
  const kind = kindOf(value);
  const isContainer = kind === "object" || kind === "array";

  const initiallyExpanded = depth < defaultExpandDepth;
  const [open, setOpen] = useState<boolean>(initiallyExpanded);
  const [decoded, setDecoded] = useState<boolean>(false);
  const [expandedString, setExpandedString] = useState(false);

  const embedded = useMemo(
    () => (kind === "string" ? tryDecodeEmbeddedJson(value) : null),
    [value, kind],
  );

  const matches = useMemo(
    () => (search ? nodeMatchesSearch(value, search) : true),
    [value, search],
  );
  const nameMatches = useMemo(() => {
    if (!search) return false;
    const q = search.toLowerCase();
    return name !== undefined && String(name).toLowerCase().includes(q);
  }, [name, search]);

  const shouldShow = !search || matches || nameMatches;
  if (!shouldShow) return null;

  const keyEl =
    name !== undefined ? (
      <span className={dark ? "text-sky-300" : "text-sky-700"}>
        {typeof name === "number" ? (
          <>{name}</>
        ) : (
          <>"{highlight(String(name), search, dark)}"</>
        )}
      </span>
    ) : null;

  const indentStyle = { paddingLeft: `${depth * 12}px` };

  const toggleAria = open ? "Collapse" : "Expand";

  const copyValue = useCallback(() => {
    let text: string;
    try {
      text =
        typeof value === "string"
          ? (value as string)
          : JSON.stringify(value, null, 2);
    } catch {
      text = String(value);
    }
    navigator.clipboard?.writeText(text);
  }, [value]);

  const copyPath = useCallback(() => {
    navigator.clipboard?.writeText(path || "$");
  }, [path]);

  if (isContainer) {
    const entries =
      kind === "array"
        ? (value as unknown[]).map((v, i) => [i, v] as [number, unknown])
        : Object.entries(value as Record<string, unknown>);
    const open_ = open;
    const bracketOpen = kind === "array" ? "[" : "{";
    const bracketClose = kind === "array" ? "]" : "}";

    const rowHoverCls = dark
      ? "hover:bg-slate-800/60"
      : "hover:bg-slate-100";

    return (
      <div className="leading-snug">
        <div
          className={`group flex items-start gap-1 ${rowHoverCls} rounded -mx-1 px-1`}
          style={indentStyle}
        >
          <button
            type="button"
            aria-label={toggleAria}
            onClick={() => setOpen((p) => !p)}
            className={`mt-0.5 inline-flex items-center justify-center w-4 h-4 select-none ${
              dark
                ? "text-slate-400 hover:text-slate-200"
                : "text-slate-500 hover:text-slate-800"
            }`}
          >
            <svg
              viewBox="0 0 12 12"
              className={`w-3 h-3 transition-transform ${open_ ? "rotate-90" : ""}`}
              fill="currentColor"
            >
              <path d="M4 2 L8 6 L4 10 Z" />
            </svg>
          </button>
          <div className="flex-1 min-w-0 break-all">
            {keyEl && (
              <>
                {keyEl}
                <span className={dark ? "text-slate-500" : "text-slate-500"}>
                  :
                </span>{" "}
              </>
            )}
            <span className={dark ? "text-slate-300" : "text-slate-700"}>
              {bracketOpen}
            </span>
            {!open_ && (
              <>
                <span
                  className={`mx-1 ${dark ? "text-slate-500" : "text-slate-400"}`}
                >
                  {summary(value)}
                </span>
                <span className={dark ? "text-slate-300" : "text-slate-700"}>
                  {bracketClose}
                  {!isLast && ","}
                </span>
              </>
            )}
            <NodeActions
              dark={dark}
              onCopyValue={copyValue}
              onCopyPath={copyPath}
            />
          </div>
        </div>
        {open_ && (
          <>
            <div>
              {entries.map(([k, v], idx) => (
                <Node
                  key={String(k)}
                  name={k as string | number}
                  value={v}
                  depth={depth + 1}
                  path={
                    kind === "array"
                      ? `${path}[${k}]`
                      : `${path}${path ? "." : ""}${String(k)}`
                  }
                  defaultExpandDepth={defaultExpandDepth}
                  dark={dark}
                  search={search}
                  isLast={idx === entries.length - 1}
                />
              ))}
            </div>
            <div
              className={dark ? "text-slate-300" : "text-slate-700"}
              style={indentStyle}
            >
              <span className="inline-block w-4" />
              {bracketClose}
              {!isLast && ","}
            </div>
          </>
        )}
      </div>
    );
  }

  const isLongString = kind === "string" && (value as string).length > 120;

  const leafHoverCls = dark
    ? "hover:bg-slate-800/60"
    : "hover:bg-slate-100";

  return (
    <div
      className={`group flex items-start gap-1 ${leafHoverCls} rounded -mx-1 px-1 leading-snug`}
      style={indentStyle}
    >
      <span className="inline-block w-4 flex-shrink-0" />
      <div className="flex-1 min-w-0 break-all">
        {keyEl && (
          <>
            {keyEl}
            <span className={dark ? "text-slate-500" : "text-slate-500"}>:</span>{" "}
          </>
        )}
        {kind === "string" ? (
          <StringLeaf
            value={value as string}
            dark={dark}
            search={search}
            isLast={isLast}
            isLong={isLongString}
            expanded={expandedString}
            onToggle={() => setExpandedString((p) => !p)}
            embedded={embedded}
            decoded={decoded}
            onDecodeToggle={() => setDecoded((p) => !p)}
          />
        ) : (
          <>
            {valueLabel(value, kind, dark)}
            {!isLast && (
              <span className={dark ? "text-slate-500" : "text-slate-500"}>
                ,
              </span>
            )}
          </>
        )}
        <NodeActions
          dark={dark}
          onCopyValue={copyValue}
          onCopyPath={copyPath}
        />
        {decoded && embedded !== null && (
          <div
            className={`mt-1 ml-2 border-l-2 pl-2 ${
              dark ? "border-slate-600" : "border-slate-300"
            }`}
          >
            <div
              className={`text-[10px] uppercase tracking-wide mb-1 ${
                dark ? "text-slate-400" : "text-slate-500"
              }`}
            >
              Decoded JSON
            </div>
            <Node
              value={embedded}
              depth={0}
              path={`${path}#decoded`}
              defaultExpandDepth={defaultExpandDepth}
              dark={dark}
              search={search}
              isLast
            />
          </div>
        )}
      </div>
    </div>
  );
}

function StringLeaf({
  value,
  dark,
  search,
  isLast,
  isLong,
  expanded,
  onToggle,
  embedded,
  decoded,
  onDecodeToggle,
}: {
  value: string;
  dark: boolean;
  search: string;
  isLast: boolean;
  isLong: boolean;
  expanded: boolean;
  onToggle: () => void;
  embedded: unknown | null;
  decoded: boolean;
  onDecodeToggle: () => void;
}) {
  const showFull = !isLong || expanded;
  const display = showFull ? value : value.slice(0, 120) + "…";
  const hasNewlines = value.includes("\n");

  return (
    <>
      <span
        className={dark ? "text-emerald-300" : "text-emerald-700"}
        title={`${value.length} characters`}
      >
        "
        {showFull && hasNewlines ? (
          <span className="whitespace-pre-wrap">
            {highlight(display, search, dark)}
          </span>
        ) : (
          highlight(display, search, dark)
        )}
        "
      </span>
      {!isLast && (
        <span className={dark ? "text-slate-500" : "text-slate-500"}>,</span>
      )}
      {isLong && (
        <button
          type="button"
          onClick={onToggle}
          className={`ml-2 text-[10px] uppercase tracking-wide ${
            dark
              ? "text-slate-400 hover:text-slate-200"
              : "text-slate-500 hover:text-slate-800"
          }`}
        >
          {expanded ? "show less" : `show all (${value.length})`}
        </button>
      )}
      {embedded !== null && (
        <button
          type="button"
          onClick={onDecodeToggle}
          className={`ml-2 text-[10px] uppercase tracking-wide ${
            dark
              ? "text-indigo-300 hover:text-indigo-100"
              : "text-indigo-600 hover:text-indigo-800"
          }`}
          title="This string contains JSON. Toggle to view it as a tree."
        >
          {decoded ? "hide JSON" : "parse as JSON"}
        </button>
      )}
    </>
  );
}

function NodeActions({
  dark,
  onCopyValue,
  onCopyPath,
}: {
  dark: boolean;
  onCopyValue: () => void;
  onCopyPath: () => void;
}) {
  const cls = `opacity-0 group-hover:opacity-100 ml-2 text-[10px] uppercase tracking-wide ${
    dark ? "text-slate-400 hover:text-slate-100" : "text-slate-500 hover:text-slate-800"
  }`;
  return (
    <span className="inline-flex gap-2 align-middle">
      <button type="button" onClick={onCopyValue} className={cls} title="Copy value">
        copy
      </button>
      <button type="button" onClick={onCopyPath} className={cls} title="Copy path">
        path
      </button>
    </span>
  );
}

export function JsonViewer({
  data,
  defaultExpandDepth = 2,
  dark = true,
  showToolbar = true,
  maxHeight,
  className,
}: JsonViewerProps) {
  const [raw, setRaw] = useState(false);
  const [search, setSearch] = useState("");
  const [expandKey, setExpandKey] = useState(0);
  const [forcedDepth, setForcedDepth] = useState(defaultExpandDepth);
  const [copied, setCopied] = useState(false);

  const rawText = useMemo(() => {
    try {
      return JSON.stringify(data, null, 2);
    } catch {
      return String(data);
    }
  }, [data]);

  const handleCopyAll = useCallback(() => {
    navigator.clipboard?.writeText(rawText).then(
      () => {
        setCopied(true);
        setTimeout(() => setCopied(false), 1200);
      },
      () => undefined,
    );
  }, [rawText]);

  const expandAll = () => {
    setForcedDepth(64);
    setExpandKey((k) => k + 1);
  };
  const collapseAll = () => {
    setForcedDepth(0);
    setExpandKey((k) => k + 1);
  };
  const resetExpand = () => {
    setForcedDepth(defaultExpandDepth);
    setExpandKey((k) => k + 1);
  };

  const containerCls = [
    "rounded border font-mono text-xs",
    dark
      ? "bg-slate-900 border-slate-700 text-slate-100"
      : "bg-slate-50 border-slate-200 text-slate-800",
    className ?? "",
  ].join(" ");

  const toolbarBtn = (extra = "") =>
    [
      "px-2 py-1 rounded text-[11px] font-sans transition-colors",
      dark
        ? "bg-slate-800 hover:bg-slate-700 text-slate-200 border border-slate-700"
        : "bg-white hover:bg-slate-100 text-slate-700 border border-slate-300",
      extra,
    ].join(" ");

  return (
    <div className={containerCls}>
      {showToolbar && (
        <div
          className={`flex flex-wrap items-center gap-2 px-2 py-2 border-b ${
            dark ? "border-slate-700" : "border-slate-200"
          }`}
        >
          <input
            type="search"
            placeholder="Search keys & values…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className={`flex-1 min-w-[140px] px-2 py-1 rounded text-[12px] font-sans ${
              dark
                ? "bg-slate-800 border border-slate-700 text-slate-100 placeholder-slate-400"
                : "bg-white border border-slate-300 text-slate-800 placeholder-slate-400"
            }`}
          />
          <button type="button" onClick={expandAll} className={toolbarBtn()}>
            Expand all
          </button>
          <button type="button" onClick={collapseAll} className={toolbarBtn()}>
            Collapse all
          </button>
          <button type="button" onClick={resetExpand} className={toolbarBtn()}>
            Reset
          </button>
          <button
            type="button"
            onClick={() => setRaw((p) => !p)}
            className={toolbarBtn()}
            aria-pressed={raw}
          >
            {raw ? "Tree view" : "Raw view"}
          </button>
          <button
            type="button"
            onClick={handleCopyAll}
            className={toolbarBtn(
              dark ? "!bg-indigo-600 !border-indigo-500 !text-white hover:!bg-indigo-500" : "!bg-indigo-600 !border-indigo-600 !text-white hover:!bg-indigo-700",
            )}
          >
            {copied ? "Copied!" : "Copy JSON"}
          </button>
        </div>
      )}
      <div
        className="overflow-auto p-2"
        style={maxHeight ? { maxHeight } : undefined}
      >
        {raw ? (
          <pre
            className={`whitespace-pre-wrap break-all ${
              dark ? "text-emerald-300" : "text-slate-800"
            }`}
          >
            {rawText}
          </pre>
        ) : (
          <Node
            key={expandKey}
            value={data as JsonValue}
            depth={0}
            path=""
            defaultExpandDepth={forcedDepth}
            dark={dark}
            search={search}
            isLast
          />
        )}
      </div>
    </div>
  );
}

export default JsonViewer;
