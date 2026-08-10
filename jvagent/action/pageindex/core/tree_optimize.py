"""Tree optimization: merge and expand driven by worst-case search cost.

Refines a PageIndex tree so that navigating it is never more expensive than
necessary. Search cost is measured in pages, routing cost R(v) = 1 page:

    S(v)          pages to linearly scan if v is collapsed = the whole subtree span
    R(v)          cost of visiting v for routing (title, summary, child descriptions)
    S_residual(v) source pages of v covered by no child

expand() - for a collapsed node, one-step lookahead, children treated as collapsed:

    trigger:       S(v) > TRIGGER_PAGES        (cost control on generation, not the rule)
    collapse_cost = S(v)
    expand_cost   = R(v) + max(S_residual(v), max_i S(c_i))
    expand iff     expand_cost < collapse_cost                  (ties keep collapsed)
    expand_gain   = collapse_cost - expand_cost

merge() - for a node that already has a subtree, decided bottom-up:

    merge_cost    = S(v)
    tree_cost(v)  = S(v)                                           if v is a frontier node
                   = R(v) + max(S_residual(v), max_c tree_cost(c))  if v is expanded
    merge iff      merge_cost <= tree_cost(v)                       (ties merge)
    merge_gain    = tree_cost(v) - merge_cost

tree_cost has an equivalent frontier form, computed independently here and
cross-checked against the recursion on every merge decision:

    tree_cost(v) = max over frontier u [ d(v, u) + S(u) ]

A node with residual pages contributes a virtual frontier entry one hop below
itself. The maximum is over every branch - the deepest leaf need not be the most
expensive one.

When a subtree is merged away, the removed titles are kept on the parent as
`key_items`: the pages stay reachable by scanning the parent, but the titles
are routing information that would otherwise be lost.

merge_same_page() runs first, as a special case of the same idea. Retrieval is
page-granular, so frontier siblings covering identical pages cannot be told apart:
an agent routed to any of them reads the same text, and because the leaf summary
prompt sees only that text, their summaries come back near-identical. They collapse
into one node titled with the union of theirs, which a leaf summary call rewrites
when the node is large enough to earn one.

merge is deterministic and needs no LLM; expand proposes subsections with the
model configured as `summary_model` (falling back to `model`) in config.yaml.
"""

import json
import os
import re
import sys
from types import SimpleNamespace

from .utils import ConfigLoader, llm_acompletion, strip_internal_keys

TRIGGER_PAGES = 5
ROUTING_COST = 1
PAGE_CHARS = 6000
TITLE_MAX_CHARS = 200

EXPAND_PROMPT = """You are splitting an over-long section of a PDF into its subsections.

Section title: {title}
Pages: {start}-{end}

{pages}

List the subsection headings that BEGIN within these pages, in document order,
each with the page number it begins on. Rules:

- Use only headings printed in the document. Never invent or paraphrase one.
- A running header, a table column label, a table row label, or a cross-reference
  is not a subsection heading.
- If this section is continuous prose, or a single table spanning the pages,
  return an empty list. That is a valid and expected answer.
- Do not include the section's own title.

Reply with JSON only:
{{"subsections": [{{"title": "<verbatim heading>", "page": <int>}}]}}"""


def _is_openai_model(model):
    """Check if a model name looks like an OpenAI model (no provider prefix or openai/ prefix)."""
    if "/" not in model:
        return True
    return model.startswith("openai/")


def note(enabled, message):
    if enabled:
        print(message, file=sys.stderr, flush=True)


def normalize(text):
    return re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()


def flatten(nodes, parent=None):
    for node in nodes:
        yield node, parent
        yield from flatten(node.get("nodes") or [], node)


def extract_json(content):
    """Pull a JSON object out of a model reply, fenced or not."""
    if not content:
        raise ValueError("model returned no content")
    text = content.strip()
    if "```" in text:
        text = re.sub(r"^.*?```(?:json)?\s*", "", text, flags=re.S)
        text = text.split("```")[0]
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError(f"no JSON object in reply: {content[:200]!r}")
    return json.loads(text[start : end + 1])


async def ask_model(model, prompt):
    return extract_json(await llm_acompletion(model, prompt))


def load_pages(pdf_path):
    """Per-page text, and per-page lines ordered top to bottom."""
    import pymupdf

    doc = pymupdf.open(pdf_path)
    text, lines = [], []
    for page in doc:
        text.append(page.get_text())
        ordered = []
        for block in page.get_text("dict")["blocks"]:
            for line in block.get("lines", []):
                content = "".join(s["text"] for s in line["spans"]).strip()
                if content:
                    ordered.append((line["bbox"][1], content))
        ordered.sort()
        lines.append([c for _, c in ordered])
    return text, lines


def subtree_end(node):
    end = node["end_index"]
    for child, _ in flatten(node.get("nodes") or []):
        end = max(end, child["end_index"])
    return end


def is_frontier(node):
    return not node.get("nodes")


def heading_at_page_start(lines, page_no, heading):
    page = lines[page_no - 1]
    if not page:
        return False
    return normalize(heading) in normalize(page[0])


def assign_ends(node, children, lines):
    sized = [dict(c) for c in children]
    old_end = subtree_end(node)
    for index, child in enumerate(sized):
        if index + 1 < len(sized):
            nxt = sized[index + 1]
            if heading_at_page_start(lines, nxt["start_index"], nxt["title"]):
                child["end_index"] = max(child["start_index"], nxt["start_index"] - 1)
            else:
                child["end_index"] = nxt["start_index"]
        else:
            child["end_index"] = old_end
    return sized


def attach_children(node, children, lines):
    sized = assign_ends(node, children, lines)
    node["nodes"] = sized
    return sized


def relabel(structure, width=4):
    mapping = {}
    for counter, (node, _) in enumerate(flatten(structure)):
        old = node.get("node_id")
        new = f"{counter:0{width}d}"
        if old is not None:
            mapping[old] = new
        node["node_id"] = new
    return mapping


def pages_of(node):
    return set(range(node["start_index"], subtree_end(node) + 1))


def S(node):
    return subtree_end(node) - node["start_index"] + 1


def S_residual(node):
    children = node.get("nodes") or []
    if not children:
        return S(node)
    covered = set()
    for child in children:
        covered |= pages_of(child)
    return len(pages_of(node) - covered)


def tree_cost(node, routing=ROUTING_COST):
    if is_frontier(node):
        return S(node)
    branches = [tree_cost(c, routing) for c in node["nodes"]]
    residual = S_residual(node)
    if residual:
        branches.append(residual)
    return routing + max(branches)


def frontier_costs(node, distance=0):
    if is_frontier(node):
        return [(distance, S(node), node.get("node_id"))]
    entries = []
    residual = S_residual(node)
    if residual:
        entries.append((distance + 1, residual, f"{node.get('node_id')}:residual"))
    for child in node["nodes"]:
        entries.extend(frontier_costs(child, distance + 1))
    return entries


def tree_cost_via_frontier(node, routing=ROUTING_COST):
    entries = frontier_costs(node)
    return max(d * routing + s for d, s, _ in entries) if entries else 0


def expand_cost(node, children, routing=ROUTING_COST):
    covered = set()
    for child in children:
        covered |= set(range(child["start_index"], child["end_index"] + 1))
    residual = len(pages_of(node) - covered)
    scans = [child["end_index"] - child["start_index"] + 1 for child in children]
    return routing + max([residual] + scans), residual


def frontier_nodes(structure, root_depth=1):
    found = []

    def visit(node, depth):
        if is_frontier(node):
            found.append((node, depth))
            return
        for child in node["nodes"]:
            visit(child, depth + 1)
        if S_residual(node):
            found.append((node, depth + 1))

    for root in structure:
        visit(root, root_depth)
    return found


def pages(node):
    return S_residual(node) if not is_frontier(node) else S(node)


def worst_case_search_complexity(structure, root_depth=1, routing=ROUTING_COST):
    entries = frontier_nodes(structure, root_depth)
    if not entries:
        return 0
    return max(depth * routing + pages(node) for node, depth in entries)


def average_search_complexity(
    structure, total_pages, root_depth=1, routing=ROUTING_COST
):
    if not total_pages:
        return 0.0, 0.0
    total = 0.0
    weight = 0.0
    for node, depth in frontier_nodes(structure, root_depth):
        n = pages(node)
        p = n / total_pages
        weight += p
        total += p * (depth * routing + (n + 1) / 2)
    return total, weight


def normalized_worst_case_complexity(
    structure, total_pages, root_depth=1, routing=ROUTING_COST
):
    if not total_pages:
        return 0.0
    return worst_case_search_complexity(structure, root_depth, routing) / total_pages


METRIC_LABELS = [
    ("worst_case_search_complexity", "Worst-Case Search Complexity"),
    ("average_search_complexity", "Average Search Complexity"),
    ("normalized_worst_case_complexity", "Normalized Worst-Case Complexity"),
]


def print_metrics(heading, metrics):
    print(heading)
    for key, label in METRIC_LABELS:
        print(f"  {label:<34} {metrics[key]}")


def complexity(structure, total_pages, root_depth=1, routing=ROUTING_COST):
    entries = frontier_nodes(structure, root_depth)
    worst = worst_case_search_complexity(structure, root_depth, routing)
    average, weight = average_search_complexity(
        structure, total_pages, root_depth, routing
    )
    depths = [d for _, d in entries]
    return {
        "total_pages": total_pages,
        "frontier_nodes": len(entries),
        "worst_case_search_complexity": worst,
        "average_search_complexity": round(average, 3),
        "normalized_worst_case_complexity": round(
            normalized_worst_case_complexity(
                structure, total_pages, root_depth, routing
            ),
            4,
        ),
        "max_depth": max(depths) if depths else 0,
        "mean_depth": round(sum(depths) / len(depths), 2) if depths else 0,
        "probability_mass": round(weight, 4),
    }


def structural_issues(node, parent, page_count):
    issues = []
    start, end = node.get("start_index"), node.get("end_index")

    for name, value in (("start_index", start), ("end_index", end)):
        if not isinstance(value, int):
            issues.append(f"{name} is {value!r}, expected an integer")
    if not isinstance(start, int) or not isinstance(end, int):
        return issues

    if start < 1 or start > page_count:
        issues.append(f"start_index {start} outside the PDF (1-{page_count})")
    if end < 1 or end > page_count:
        issues.append(f"end_index {end} outside the PDF (1-{page_count})")
    if start > end:
        issues.append(f"start_index {start} is after end_index {end}")

    if parent and isinstance(parent.get("start_index"), int):
        if start < parent["start_index"]:
            issues.append(
                f"start_index {start} precedes parent "
                f"{parent.get('node_id')} (starts {parent['start_index']})"
            )
    return issues


def _sibling_groups(nodes, parent_id=None):
    yield parent_id, nodes
    for node in nodes:
        if node.get("nodes"):
            yield from _sibling_groups(node["nodes"], node.get("node_id"))


def ordering_issues(nodes):
    found = {}
    for _, group in _sibling_groups(nodes):
        previous = None
        for node in group:
            start = node.get("start_index")
            if (
                isinstance(start, int)
                and isinstance(previous, int)
                and start < previous
            ):
                found.setdefault(node.get("node_id"), []).append(
                    f"start_index {start} is before the preceding sibling's {previous}"
                )
            if isinstance(start, int):
                previous = start
    return found


def validate(structure, page_count):
    issues = []
    for node, parent in flatten(structure):
        for problem in structural_issues(node, parent, page_count):
            issues.append(f"[{node.get('node_id')}] {problem}")
    for node_id, problems in ordering_issues(structure).items():
        issues.extend(f"[{node_id}] {p}" for p in problems)
    covered = set()
    for node, _ in flatten(structure):
        covered |= set(range(node["start_index"], node["end_index"] + 1))
    gaps = [p for p in range(1, page_count + 1) if p not in covered]
    if gaps:
        issues.append(f"pages covered by no node: {gaps}")
    return issues


def page_label(node):
    start, end = node["start_index"], subtree_end(node)
    return f"p.{start}" if start == end else f"p.{start}-{end}"


def union_title(titles, node):
    joined = "; ".join(title for title in titles if title)
    if not joined or len(joined) > TITLE_MAX_CHARS:
        return page_label(node)
    return joined


def merge_same_page(structure, log):
    changed = False

    def visit(nodes):
        nonlocal changed
        groups = {}
        for node in nodes:
            visit(node.get("nodes") or [])
            if is_frontier(node):
                groups.setdefault((node["start_index"], subtree_end(node)), []).append(
                    node
                )

        for span, group in groups.items():
            if len(group) < 2:
                continue
            keeper, dropped = group[0], group[1:]
            titles = []
            for node in group:
                titles.append(node["title"])
                titles.extend(node.get("key_items") or [])
            log.append(
                {
                    "op": "merge_same_page",
                    "node_id": keeper.get("node_id"),
                    "pages": list(span),
                    "dropped": len(dropped),
                    "dropped_ids": [n.get("node_id") for n in dropped],
                    "key_items": titles,
                }
            )
            keeper["key_items"] = titles
            keeper["title"] = union_title(titles, keeper)
            keeper["_same_page"] = True
            for node in dropped:
                nodes.remove(node)
            changed = True

    visit(structure)
    return changed


def merge(structure, routing, log, frozen, progress=False):
    changed = False

    def visit(node):
        nonlocal changed
        if is_frontier(node):
            return
        for child in list(node.get("nodes") or []):
            visit(child)
        if is_frontier(node):
            return

        cost = tree_cost(node, routing)
        checked = tree_cost_via_frontier(node, routing)
        span = S(node)
        if span <= cost:
            removed = [c.get("node_id") for c, _ in flatten(node["nodes"])]
            titles = []
            for child, _ in flatten(node["nodes"]):
                titles.append(child["title"])
                titles.extend(child.get("key_items") or [])
            log.append(
                {
                    "op": "merge",
                    "node_id": node.get("node_id"),
                    "S": span,
                    "tree_cost": cost,
                    "frontier_cost": checked,
                    "merge_gain": cost - span,
                    "removed": len(removed),
                    "removed_ids": removed,
                    "key_items": titles,
                    "frontier": sorted(
                        frontier_costs(node, routing),
                        key=lambda e: -(e[0] * routing + e[1]),
                    )[:5],
                }
            )
            node["end_index"] = subtree_end(node)
            node.pop("nodes", None)
            if titles:
                node["key_items"] = titles
            frozen.add(node.get("node_id"))
            changed = True
            note(
                progress,
                f"    merge  {node.get('node_id') or '-':>8}  "
                f"S={span} <= tree_cost={cost}  dropped {len(removed)} node(s)",
            )

    for root in list(structure):
        visit(root)
    return changed


def merge_tree(structure):
    merge(structure, ROUTING_COST, [], set())
    return structure


def load_headings_cache(path):
    if not path or not os.path.exists(path):
        return None
    with open(path) as f:
        data = json.load(f)
    index = {}
    for record in data.get("pages") or []:
        if record.get("headings"):
            index[record["page"]] = record["headings"]
    return index


def children_from_cache(node, cache, kinds):
    if not cache:
        return []
    start, end = node["start_index"], subtree_end(node)
    out = []
    for page in range(start, end + 1):
        for heading in cache.get(page) or []:
            if kinds and heading.get("kind") not in kinds:
                continue
            if normalize(heading["title"]) == normalize(node["title"]):
                continue
            out.append(
                {
                    "title": heading["title"],
                    "start_index": page,
                    "end_index": end,
                    "node_id": f"{node['node_id']}.{len(out) + 1}",
                }
            )
    return out


async def propose_children(node, pages, args):
    start, end = node["start_index"], subtree_end(node)
    block = "\n".join(
        f"<page_{n}>\n{pages[n - 1][:PAGE_CHARS]}\n</page_{n}>"
        for n in range(start, end + 1)
    )
    answer = await ask_model(
        args.model,
        EXPAND_PROMPT.format(title=node["title"], start=start, end=end, pages=block),
    )

    accepted, seen = [], set()
    for item in answer.get("subsections") or []:
        title, page = (item or {}).get("title"), (item or {}).get("page")
        if not isinstance(page, int) or not start <= page <= end or not title:
            continue
        if normalize(title) not in normalize(pages[page - 1]):
            continue
        if normalize(title) in seen or normalize(title) == normalize(node["title"]):
            continue
        if accepted and page < accepted[-1]["start_index"]:
            continue
        seen.add(normalize(title))
        accepted.append(
            {
                "title": title.strip(),
                "start_index": page,
                "end_index": end,
                "node_id": f"{node['node_id']}.{len(accepted) + 1}",
            }
        )
    return accepted


async def expand(structure, pages, lines, args, log, frozen):
    changed = False
    queue = [n for n, _ in flatten(structure)]

    while queue:
        node = queue.pop(0)
        if not is_frontier(node) or node.get("node_id") in frozen:
            continue

        span = S(node)
        if span <= args.trigger_pages:
            continue

        note(
            args.progress,
            f"    expand {node.get('node_id'):>8}  S={span}  "
            f"pages {node['start_index']}-{subtree_end(node)} ...",
        )
        candidates = []
        cached = children_from_cache(node, args.cache, args.kinds)
        if cached:
            candidates.append(("cache", cached))

        attempts = 0
        while attempts <= args.empty_retries:
            attempts += 1
            try:
                proposed = await propose_children(node, pages, args)
            except Exception as exc:
                from .utils import _is_unrecoverable

                if _is_unrecoverable(exc):
                    raise
                log.append(
                    {
                        "op": "expand",
                        "node_id": node.get("node_id"),
                        "decision": "error",
                        "attempt": attempts,
                        "detail": f"{type(exc).__name__}: {exc}",
                    }
                )
                continue
            if proposed:
                candidates.append((f"llm:{attempts}", proposed))
                break

        if not candidates:
            note(args.progress, f"           -> no children found, kept collapsed")
            log.append(
                {
                    "op": "expand",
                    "node_id": node.get("node_id"),
                    "decision": "no_children",
                    "S": span,
                    "attempts": attempts,
                }
            )
            frozen.add(node.get("node_id"))
            continue

        scored = []
        for source, children in candidates:
            sized = assign_ends(node, children, lines)
            cost, residual = expand_cost(node, sized, args.routing)
            scored.append(
                {
                    "source": source,
                    "children": sized,
                    "expand_cost": cost,
                    "S_residual": residual,
                }
            )
        scored.sort(key=lambda s: s["expand_cost"])
        best = scored[0]

        cost = best["expand_cost"]
        gain = span - cost
        ratio = gain / span if span else 0.0
        keep = cost < span and ratio >= args.min_gain_ratio

        note(
            args.progress,
            f"           -> {len(best['children'])} children from {best['source']}, "
            f"cost {cost} vs {span}, "
            f"{'expand (gain %d)' % gain if keep else 'kept collapsed'}",
        )
        log.append(
            {
                "op": "expand",
                "node_id": node.get("node_id"),
                "decision": "expand" if keep else "keep_collapsed",
                "S": span,
                "expand_cost": cost,
                "expand_gain": gain,
                "gain_ratio": round(ratio, 3),
                "S_residual": best["S_residual"],
                "source": best["source"],
                "considered": [
                    {
                        "source": s["source"],
                        "children": len(s["children"]),
                        "expand_cost": s["expand_cost"],
                    }
                    for s in scored
                ],
                "children": [
                    {
                        "node_id": c["node_id"],
                        "title": c["title"],
                        "start_index": c["start_index"],
                        "end_index": c["end_index"],
                        "S": c["end_index"] - c["start_index"] + 1,
                    }
                    for c in best["children"]
                ],
            }
        )

        frozen.add(node.get("node_id"))
        if keep:
            changed = True
            attach_children(node, best["children"], lines)
            queue.extend(node["nodes"])
    return changed


def default_model():
    opt = ConfigLoader().load({})
    return getattr(opt, "summary_model", None) or opt.model


async def optimize(
    structure,
    pages,
    lines,
    model=None,
    routing=ROUTING_COST,
    trigger_pages=TRIGGER_PAGES,
    min_gain_ratio=0.0,
    do_merge=True,
    do_expand=True,
    max_rounds=3,
    page_count=None,
    cache=None,
    kinds=("section", "table"),
    empty_retries=1,
    do_relabel=True,
    progress=False,
):
    if do_expand and pages is None:
        raise ValueError(
            "expand needs the PDF pages; pass pages/lines or do_expand=False"
        )
    opts = SimpleNamespace(
        model=model or default_model(),
        routing=routing,
        trigger_pages=trigger_pages,
        min_gain_ratio=min_gain_ratio,
        cache=cache,
        kinds=set(kinds) if kinds else None,
        empty_retries=empty_retries,
        progress=progress,
    )
    baseline = set(validate(structure, page_count)) if page_count else set()
    before = complexity(structure, page_count, routing=routing) if page_count else {}

    log, frozen = [], set()
    rounds = 0
    for round_no in range(1, max_rounds + 1):
        rounds = round_no
        note(progress, f"  round {round_no}")
        same_page = merge_same_page(structure, log) if do_merge else False
        merged = merge(structure, routing, log, frozen, progress) if do_merge else False
        expanded = (
            await expand(structure, pages, lines, opts, log, frozen)
            if do_expand
            else False
        )
        log.append(
            {
                "op": "round",
                "round": round_no,
                "same_page": same_page,
                "merged": merged,
                "expanded": expanded,
            }
        )
        if not (same_page or merged or expanded):
            break

    id_map = relabel(structure) if do_relabel else {}
    after = complexity(structure, page_count, routing=routing) if page_count else {}
    issues = (
        [i for i in validate(structure, page_count) if i not in baseline]
        if page_count
        else []
    )
    return {
        "structure": structure,
        "log": log,
        "rounds": rounds,
        "before": before,
        "after": after,
        "id_map": id_map,
        "merges": sum(1 for e in log if e["op"] == "merge"),
        "same_page_merges": sum(1 for e in log if e["op"] == "merge_same_page"),
        "same_page_dropped": sum(
            e["dropped"] for e in log if e["op"] == "merge_same_page"
        ),
        "expands": sum(1 for e in log if e.get("decision") == "expand"),
        "kept_collapsed": sum(1 for e in log if e.get("decision") == "keep_collapsed"),
        "new_issues": issues,
    }


def optimize_tree(doc, pdf_path=None, model=None, do_expand=None, **kwargs):
    """Synchronous entry point over a loaded structure dict or a JSON path.

    `doc` is the {"structure": [...]} dict produced by the tree builders (other
    keys are preserved). Without `pdf_path` only merge runs; expand needs the
    page text. Returns the run summary; the refined tree is doc["structure"].
    """
    import asyncio

    if isinstance(doc, str):
        with open(doc) as f:
            doc = json.load(f)
    structure = doc["structure"]
    pages = lines = None
    page_count = kwargs.pop("page_count", None)
    if pdf_path:
        pages, lines = load_pages(pdf_path)
        page_count = len(pages)
    if do_expand is None:
        do_expand = pdf_path is not None
    result = asyncio.run(
        optimize(
            structure,
            pages,
            lines,
            model=model,
            page_count=page_count,
            do_expand=do_expand,
            **kwargs,
        )
    )
    strip_internal_keys(result["structure"])
    doc["structure"] = result["structure"]
    return result


def report_costs(structure, routing, trigger):
    rows = []
    for node, _ in flatten(structure):
        rows.append(
            {
                "node_id": node.get("node_id"),
                "title": node.get("title"),
                "S": S(node),
                "frontier": is_frontier(node),
                "tree_cost": tree_cost(node, routing),
                "S_residual": S_residual(node),
                "children": len(node.get("nodes") or []),
            }
        )
    merges = [r for r in rows if not r["frontier"] and r["S"] <= r["tree_cost"]]
    triggers = [r for r in rows if r["frontier"] and r["S"] > trigger]
    return rows, merges, triggers
