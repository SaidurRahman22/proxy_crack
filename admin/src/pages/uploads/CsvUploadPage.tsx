import { useMutation, useQueryClient } from '@tanstack/react-query'
import {
  AlertTriangle,
  CheckCircle2,
  FileSpreadsheet,
  RotateCcw,
  Upload,
  UploadCloud,
  X,
} from 'lucide-react'
import { useCallback, useRef, useState, type DragEvent } from 'react'
import { Link } from 'react-router-dom'

import { api } from '@/api/client'
import { ApiError } from '@/api/errors'
import type { CsvUploadResponse, RowError } from '@/api/types'
import { PageHeader } from '@/components/layout/PageHeader'
import { Button } from '@/components/ui/Button'
import { Banner, ErrorState } from '@/components/ui/States'
import { useToast } from '@/components/ui/ToastProvider'
import { formatBytes, formatNumber, pluralise } from '@/lib/format'

const CSV_TEMPLATE = 'protocol,host,port,country,city,tier,username,password,ss_method'
const MAX_BYTES = 25 * 1024 * 1024

export function CsvUploadPage() {
  const [file, setFile] = useState<File | null>(null)
  const [dragging, setDragging] = useState(false)
  const [progress, setProgress] = useState(0)
  const [result, setResult] = useState<CsvUploadResponse | null>(null)
  const [localError, setLocalError] = useState<string | null>(null)
  const inputRef = useRef<HTMLInputElement>(null)
  const abortRef = useRef<AbortController | null>(null)
  const queryClient = useQueryClient()
  const toast = useToast()

  const upload = useMutation({
    mutationFn: (selected: File) => {
      const controller = new AbortController()
      abortRef.current = controller
      setProgress(0)
      return api.proxies.uploadCsv(selected, setProgress, controller.signal)
    },
    onSuccess: (response) => {
      setResult(response)
      void queryClient.invalidateQueries({ queryKey: ['proxies'] })
      void queryClient.invalidateQueries({ queryKey: ['upload-batches'] })
      void queryClient.invalidateQueries({ queryKey: ['stats'] })
      if (response.fail_count === 0) {
        toast.success(
          'Import complete',
          `${formatNumber(response.created_count)} created, ${formatNumber(response.updated_count)} updated.`,
        )
      } else {
        toast.info(
          'Import finished with errors',
          `${formatNumber(response.fail_count)} ${pluralise(response.fail_count, 'row')} rejected.`,
        )
      }
    },
  })

  const accept = useCallback((selected: File | null) => {
    setResult(null)
    setLocalError(null)
    upload.reset()
    if (!selected) {
      setFile(null)
      return
    }
    if (!selected.name.toLowerCase().endsWith('.csv')) {
      setFile(null)
      setLocalError(`“${selected.name}” is not a .csv file.`)
      return
    }
    if (selected.size > MAX_BYTES) {
      setFile(null)
      setLocalError(
        `“${selected.name}” is ${formatBytes(selected.size)}, over the ${formatBytes(MAX_BYTES, 0)} limit.`,
      )
      return
    }
    setFile(selected)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  function onDrop(event: DragEvent<HTMLElement>): void {
    event.preventDefault()
    setDragging(false)
    accept(event.dataTransfer.files[0] ?? null)
  }

  function reset(): void {
    abortRef.current?.abort()
    setFile(null)
    setResult(null)
    setProgress(0)
    setLocalError(null)
    upload.reset()
    if (inputRef.current) inputRef.current.value = ''
  }

  return (
    <>
      <PageHeader
        title="CSV import"
        description="Bulk-load a provider list. Rows that fail validation are reported line by line; the rest are still imported."
        actions={
          <Link className="btn btn--ghost" to="/upload-batches">
            Past batches
          </Link>
        }
      />

      <div className="dash">
        <section className="card span-7">
          <div className="card__header">
            <h2 className="card__title">Upload a file</h2>
            {(file || result) && (
              <Button variant="quiet" size="sm" icon={<RotateCcw size={14} />} onClick={reset}>
                Start over
              </Button>
            )}
          </div>

          <div className="card__body stack">
            {localError && (
              <Banner tone="danger" role="alert" icon={<AlertTriangle size={15} aria-hidden="true" />}>
                {localError}
              </Banner>
            )}

            {!file ? (
              <>
                <input
                  ref={inputRef}
                  type="file"
                  accept=".csv,text/csv"
                  className="visually-hidden"
                  id="csv-input"
                  onChange={(event) => accept(event.target.files?.[0] ?? null)}
                />
                {/*
                  The dropzone is a <label> for a real file input, so clicking,
                  tabbing and pressing Enter all open the picker without any
                  custom key handling.
                */}
                <label
                  className="dropzone"
                  htmlFor="csv-input"
                  data-dragging={dragging || undefined}
                  onDragOver={(event) => {
                    event.preventDefault()
                    setDragging(true)
                  }}
                  onDragLeave={() => setDragging(false)}
                  onDrop={onDrop}
                >
                  <UploadCloud size={28} className="dropzone__icon" aria-hidden="true" />
                  <span className="dropzone__title">Drop a CSV here, or choose a file</span>
                  <span style={{ fontSize: 'var(--text-sm)' }}>
                    Up to {formatBytes(MAX_BYTES, 0)}. Existing host:port pairs are updated, not duplicated.
                  </span>
                </label>
              </>
            ) : (
              <div className="stack" style={{ gap: 'var(--space-sm)' }}>
                <div className="picker-selected">
                  <span className="row" style={{ minWidth: 0 }}>
                    <FileSpreadsheet size={16} aria-hidden="true" style={{ color: 'var(--accent)' }} />
                    <span className="truncate">{file.name}</span>
                    <span className="muted">{formatBytes(file.size)}</span>
                  </span>
                  {!upload.isPending && (
                    <Button variant="quiet" size="sm" iconOnly icon={<X size={14} />} onClick={reset}>
                      Remove file
                    </Button>
                  )}
                </div>

                {upload.isPending && (
                  <div className="stack" style={{ gap: 'var(--space-2xs)' }}>
                    <div className="progress">
                      <div
                        className="progress__bar"
                        style={{ width: `${Math.round(progress * 100)}%` }}
                        role="progressbar"
                        aria-valuenow={Math.round(progress * 100)}
                        aria-valuemin={0}
                        aria-valuemax={100}
                        aria-label="Upload progress"
                      />
                    </div>
                    <span className="muted" style={{ fontSize: 'var(--text-xs)' }} aria-live="polite">
                      {progress < 1
                        ? `Uploading… ${Math.round(progress * 100)}%`
                        : 'Uploaded. Parsing rows on the server…'}
                    </span>
                  </div>
                )}

                {!result && (
                  <div className="row">
                    <Button
                      variant="primary"
                      icon={<Upload size={15} />}
                      loading={upload.isPending}
                      onClick={() => upload.mutate(file)}
                    >
                      Import {file.name}
                    </Button>
                    {upload.isPending && (
                      <Button variant="ghost" onClick={() => abortRef.current?.abort()}>
                        Cancel
                      </Button>
                    )}
                  </div>
                )}
              </div>
            )}

            {upload.isError && (
              <ErrorState
                error={upload.error}
                title={
                  upload.error instanceof ApiError && upload.error.status === 429
                    ? 'Import rate limit reached'
                    : 'The import was rejected'
                }
                // Retrying into a 429 just burns another request.
                onRetry={
                  file && !(upload.error instanceof ApiError && upload.error.status === 429)
                    ? () => upload.mutate(file)
                    : undefined
                }
              />
            )}

            {result && <ImportResult result={result} />}
          </div>
        </section>

        <section className="card span-5">
          <div className="card__header">
            <h2 className="card__title">Expected format</h2>
          </div>
          <div className="card__body stack">
            <p className="muted" style={{ fontSize: 'var(--text-sm)' }}>
              A header row is required. <code className="mono">protocol</code>,{' '}
              <code className="mono">host</code>, <code className="mono">port</code> and{' '}
              <code className="mono">country</code> are mandatory; the rest may be blank.
            </p>
            <pre className="code-block">
              <code>
                {CSV_TEMPLATE}
                {'\n'}
                socks5,203.0.113.7,1080,NL,Amsterdam,premium,user,secret,
                {'\n'}
                ss,198.51.100.22,8388,DE,Frankfurt,premium,,secret,aes-256-gcm
                {'\n'}
                http,203.0.113.90,8080,US,Dallas,free,,,
              </code>
            </pre>
            <dl className="kv">
              <dt>protocol</dt>
              <dd className="mono">ss · socks5 · http</dd>
              <dt>tier</dt>
              <dd className="mono">free · premium (defaults to premium)</dd>
              <dt>country</dt>
              <dd>Two-letter ISO code</dd>
              <dt>ss_method</dt>
              <dd>Required when protocol is ss</dd>
            </dl>
            <Banner tone="warning" icon={<AlertTriangle size={15} aria-hidden="true" />}>
              An HTTP hop cannot carry UDP. Chains containing one are TCP-only.
            </Banner>
          </div>
        </section>
      </div>
    </>
  )
}

function ImportResult({ result }: { result: CsvUploadResponse }) {
  const clean = result.fail_count === 0
  return (
    <div className="stack" style={{ gap: 'var(--space-sm)' }}>
      <Banner
        tone={clean ? 'accent' : 'warning'}
        role="status"
        icon={
          clean ? (
            <CheckCircle2 size={15} aria-hidden="true" />
          ) : (
            <AlertTriangle size={15} aria-hidden="true" />
          )
        }
      >
        {clean ? (
          <>
            All {formatNumber(result.row_count)} {pluralise(result.row_count, 'row')} imported as
            batch <strong className="mono">#{result.batch_id}</strong>.
          </>
        ) : (
          <>
            Batch <strong className="mono">#{result.batch_id}</strong>:{' '}
            {formatNumber(result.fail_count)} of {formatNumber(result.row_count)}{' '}
            {pluralise(result.row_count, 'row')} were rejected. The rest were imported.
          </>
        )}
      </Banner>

      <div className="result-grid">
        <div className="result-tile">
          <div className="result-tile__value">{formatNumber(result.row_count)}</div>
          <div className="result-tile__label">Rows read</div>
        </div>
        <div className="result-tile result-tile--ok">
          <div className="result-tile__value">{formatNumber(result.created_count)}</div>
          <div className="result-tile__label">Created</div>
        </div>
        <div className="result-tile">
          <div className="result-tile__value">{formatNumber(result.updated_count)}</div>
          <div className="result-tile__label">Updated</div>
        </div>
        <div className={result.fail_count > 0 ? 'result-tile result-tile--danger' : 'result-tile'}>
          <div className="result-tile__value">{formatNumber(result.fail_count)}</div>
          <div className="result-tile__label">Failed</div>
        </div>
      </div>

      {result.errors.length > 0 && <RowErrorReport errors={result.errors} />}

      <div className="row">
        <Link className="btn btn--secondary btn--sm" to="/proxies">
          View the proxy pool
        </Link>
        <Link className="btn btn--ghost btn--sm" to="/upload-batches">
          Batch history
        </Link>
      </div>
    </div>
  )
}

/** Per-line report, sorted by line number so it reads like the source file. */
export function RowErrorReport({ errors }: { errors: RowError[] }) {
  const sorted = [...errors].sort((a, b) => a.line - b.line)
  return (
    <div className="stack" style={{ gap: 'var(--space-2xs)' }}>
      <h3 className="eyebrow">
        {formatNumber(sorted.length)} rejected {pluralise(sorted.length, 'row')}
      </h3>
      <div className="errors-list">
        {sorted.map((error, index) => (
          <div className="error-row" key={`${error.line}-${index}`}>
            <span className="error-row__line">line {error.line}</span>
            <span className="error-row__reason">{error.reason}</span>
          </div>
        ))}
      </div>
    </div>
  )
}
