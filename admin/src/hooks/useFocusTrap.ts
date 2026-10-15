import { useEffect, type RefObject } from 'react'

const FOCUSABLE = [
  'a[href]',
  'button:not([disabled])',
  'input:not([disabled]):not([type="hidden"])',
  'select:not([disabled])',
  'textarea:not([disabled])',
  '[tabindex]:not([tabindex="-1"])',
].join(',')

function focusable(container: HTMLElement): HTMLElement[] {
  return Array.from(container.querySelectorAll<HTMLElement>(FOCUSABLE)).filter(
    (element) => element.offsetParent !== null || element === document.activeElement,
  )
}

/**
 * Keeps Tab inside an open dialog, moves focus in on mount, and restores it to
 * the trigger on unmount. Escape is handled here too so every overlay in the
 * app dismisses the same way.
 */
export function useFocusTrap(
  ref: RefObject<HTMLElement | null>,
  active: boolean,
  onDismiss?: () => void,
): void {
  useEffect(() => {
    if (!active) return
    const container = ref.current
    if (!container) return

    const previous = document.activeElement instanceof HTMLElement ? document.activeElement : null

    // Prefer the first real control; fall back to the container itself so the
    // dialog is still announced and Escape still works.
    const initial = focusable(container)[0] ?? container
    if (initial === container) container.setAttribute('tabindex', '-1')
    initial.focus()

    function handleKeyDown(event: KeyboardEvent): void {
      if (event.key === 'Escape' && onDismiss) {
        event.stopPropagation()
        onDismiss()
        return
      }
      if (event.key !== 'Tab') return
      const node = ref.current
      if (!node) return

      const items = focusable(node)
      if (items.length === 0) {
        event.preventDefault()
        return
      }
      const first = items[0]!
      const last = items[items.length - 1]!
      const current = document.activeElement

      if (event.shiftKey && (current === first || !node.contains(current))) {
        event.preventDefault()
        last.focus()
      } else if (!event.shiftKey && current === last) {
        event.preventDefault()
        first.focus()
      }
    }

    document.addEventListener('keydown', handleKeyDown, true)
    const { overflow } = document.body.style
    document.body.style.overflow = 'hidden'

    return () => {
      document.removeEventListener('keydown', handleKeyDown, true)
      document.body.style.overflow = overflow
      previous?.focus()
    }
  }, [ref, active, onDismiss])
}
