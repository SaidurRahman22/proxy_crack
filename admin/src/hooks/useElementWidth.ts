import { useEffect, useState, type RefObject } from 'react'

/**
 * Observed width of an element, for charts that need real pixel geometry
 * rather than a distorted `preserveAspectRatio="none"` viewBox.
 */
export function useElementWidth(ref: RefObject<HTMLElement | null>, fallback = 640): number {
  const [width, setWidth] = useState(fallback)

  useEffect(() => {
    const element = ref.current
    if (!element) return

    setWidth(element.clientWidth || fallback)
    const observer = new ResizeObserver((entries) => {
      const entry = entries[0]
      if (entry) setWidth(entry.contentRect.width)
    })
    observer.observe(element)
    return () => observer.disconnect()
  }, [ref, fallback])

  return width
}
