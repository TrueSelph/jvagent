declare module 'd3-graphviz' {
  export interface GraphvizInstance {
    zoom(enable: boolean): GraphvizInstance
    fit(fit: boolean): GraphvizInstance
    width(w: number): GraphvizInstance
    height(h: number): GraphvizInstance
    zoomScaleExtent(extent: [number, number]): GraphvizInstance
    transition(factory: () => unknown): GraphvizInstance
    onerror(callback: (err: unknown) => void): GraphvizInstance
    renderDot(src: string, callback?: () => void): GraphvizInstance
    zoomBehavior(): { scaleBy: (s: unknown, k: number) => void; transform: (s: unknown, t: unknown) => void } | null
    zoomSelection(): unknown
  }

  export function graphviz(
    selector: string | Element,
    options?: boolean | object
  ): GraphvizInstance
}
