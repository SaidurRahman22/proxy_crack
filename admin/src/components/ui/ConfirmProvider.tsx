import { AlertTriangle } from 'lucide-react'
import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from 'react'

import { Button } from './Button'
import { Overlay } from './Overlay'

export interface ConfirmOptions {
  title: string
  body?: ReactNode
  confirmLabel?: string
  cancelLabel?: string
  tone?: 'danger' | 'primary'
  /**
   * Requires the operator to type this string before confirm is enabled.
   * Reserved for irreversible bulk operations, where a misclick is expensive.
   */
  requireText?: string
}

type Confirm = (options: ConfirmOptions) => Promise<boolean>

const ConfirmContext = createContext<Confirm | null>(null)

/**
 * Promise-based confirmation, so every destructive path reads the same:
 *
 *   if (!(await confirm({ ... }))) return
 *
 * There is exactly one dialog instance, so confirmations cannot stack.
 */
export function ConfirmProvider({ children }: { children: ReactNode }) {
  const [options, setOptions] = useState<ConfirmOptions | null>(null)
  const [typed, setTyped] = useState('')
  const resolver = useRef<((value: boolean) => void) | null>(null)

  const settle = useCallback((value: boolean) => {
    resolver.current?.(value)
    resolver.current = null
    setOptions(null)
    setTyped('')
  }, [])

  const confirm = useCallback<Confirm>((next) => {
    // A second request while one is open resolves the first as cancelled.
    resolver.current?.(false)
    setTyped('')
    setOptions(next)
    return new Promise<boolean>((resolve) => {
      resolver.current = resolve
    })
  }, [])

  const value = useMemo(() => confirm, [confirm])
  const blocked = options?.requireText !== undefined && typed.trim() !== options.requireText

  return (
    <ConfirmContext.Provider value={value}>
      {children}
      <Overlay
        open={options !== null}
        onClose={() => settle(false)}
        title={options?.title ?? ''}
        footer={
          <>
            <Button variant="ghost" onClick={() => settle(false)}>
              {options?.cancelLabel ?? 'Cancel'}
            </Button>
            <Button
              variant={options?.tone === 'primary' ? 'primary' : 'danger'}
              onClick={() => settle(true)}
              disabled={blocked}
            >
              {options?.confirmLabel ?? 'Confirm'}
            </Button>
          </>
        }
      >
        <div className="row" style={{ alignItems: 'flex-start', gap: 'var(--space-sm)' }}>
          <span
            className={options?.tone === 'primary' ? 'state__icon' : 'state__icon state__icon--danger'}
            style={{ width: '2.25rem', height: '2.25rem', marginBottom: 0 }}
            aria-hidden="true"
          >
            <AlertTriangle size={17} />
          </span>
          <div className="subtle" style={{ fontSize: 'var(--text-sm)', minWidth: 0 }}>
            {options?.body}
          </div>
        </div>

        {options?.requireText !== undefined && (
          <div className="field">
            <label className="field__label" htmlFor="confirm-phrase">
              Type <code className="mono">{options.requireText}</code> to continue
            </label>
            <input
              id="confirm-phrase"
              className="input"
              value={typed}
              autoComplete="off"
              spellCheck={false}
              onChange={(event) => setTyped(event.target.value)}
            />
          </div>
        )}
      </Overlay>
    </ConfirmContext.Provider>
  )
}

export function useConfirm(): Confirm {
  const context = useContext(ConfirmContext)
  if (!context) throw new Error('useConfirm must be used inside <ConfirmProvider>')
  return context
}
