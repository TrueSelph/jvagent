export function preserveScroll(fn: () => void): void {
  if (typeof window === "undefined") {
    fn()
    return
  }
  const y = window.scrollY || window.pageYOffset || 0
  fn()
  setTimeout(() => window.scrollTo({ top: y, behavior: "auto" }), 0)
}
