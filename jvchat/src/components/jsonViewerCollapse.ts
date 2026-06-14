/** Initial expand state for JsonViewer container nodes after toolbar-driven remounts. */
export function computeInitiallyExpanded(
  depth: number,
  forcedDepth: number,
  path: string,
  collapsedPaths: ReadonlySet<string>,
  collapseGateDepth: number,
): boolean {
  if (depth >= forcedDepth) return false;
  if (!path || collapsedPaths.size === 0) return true;
  const pathCollapsed =
    collapsedPaths.has(path) && forcedDepth <= collapseGateDepth;
  return !pathCollapsed;
}
