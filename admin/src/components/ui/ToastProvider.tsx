import { AlertCircle, CheckCircle2, Info, X } from 'lucide-react'
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from 'react'

import { cn } from '@/lib/cn'

export type ToastTone = 'success' | 'error' | 'info'

interface Toast {
  id: number
  tone: ToastTone
  title: string
  body?: string
}

interface ToastApi {
  success: (title: string, body?: string) => void
  error: (title: string, body?: string) => void
  info: (title: string, body?: string) => void
}

const ToastContext = createContext<ToastApi | null>(null)

const ICONS: Record<ToastTone, typeof CheckCircle2> = {
  success: CheckCircle2,
  error: AlertCircle,
  info: Info,
}

/** Errors linger; successes get out of the way. */
const TTL: Record<ToastTone, number> = {
  success: 3800,
  info: 4500,
  error: 8000,
}

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([])
  const nextId = useRef(1)
  const timers = useRef(new Map<number, ReturnType<typeof setTimeout>>())

  const dismiss = useCallback((id: number) => {
    const timer = timers.current.get(id)
    if (timer) {
      clearTimeout(timer)
      timers.current.delete(id)
    }
    setToasts((current) => current.filter((toast) => toast.id !== id))
  }, [])

  const push = useCallback(
    (tone: ToastTone, title: string, body?: string) => {
      const id = nextId.current
      nextId.current += 1
      setToasts((current) => [...current.slice(-3), { id, tone, title, body }])
      timers.current.set(
        id,
        setTimeout(() => dismiss(id), TTL[tone]),
      )
    },
    [dismiss],
  )

  useEffect(() => {
    const pending = timers.current
    return () => {
      for (const timer of pending.values()) clearTimeout(timer)
      pending.clear()
    }
  }, [])

  const api = useMemo<ToastApi>(
    () => ({
      success: (title, body) => push('success', title, body),
      error: (title, body) => push('error', title, body),
      info: (title, body) => push('info', title, body),
    }),
    [push],
  )

  return (
    <ToastContext.Provider value={api}>
      {children}
      {/*
        Successes are polite; failures are assertive, because an operator who
        just clicked "delete 40 proxies" needs to hear that it failed.
      */}
      <div className="toasts">
        {toasts.map((toast) => {
          const Icon = ICONS[toast.tone]
          return (
            <div
              key={toast.id}
              className={cn('toast', `toast--${toast.tone}`)}
              role={toast.tone === 'error' ? 'alert' : 'status'}
              aria-live={toast.tone === 'error' ? 'assertive' : 'polite'}
            >
              <Icon size={15} className={`toast__icon--${toast.tone}`} aria-hidden="true" />
              <div className="toast__text">
                <div className="toast__title">{toast.title}</div>
                {toast.body && <div>{toast.body}</div>}
              </div>
              <button
                type="button"
                className="btn btn--quiet btn--sm btn--icon"
                onClick={() => dismiss(toast.id)}
              >
                <X size={14} aria-hidden="true" />
                <span className="visually-hidden">Dismiss notification</span>
              </button>
            </div>
          )
        })}
      </div>
    </ToastContext.Provider>
  )
}

export function useToast(): ToastApi {
  const context = useContext(ToastContext)
  if (!context) throw new Error('useToast must be used inside <ToastProvider>')
  return context
}
