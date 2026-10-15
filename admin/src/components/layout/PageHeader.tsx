import type { ReactNode } from 'react'

export function PageHeader({
  title,
  description,
  actions,
}: {
  title: string
  description?: ReactNode
  actions?: ReactNode
}) {
  return (
    <div className="page__head">
      <div className="page__heading">
        <h2 className="page__title">{title}</h2>
        {description && <p className="page__description">{description}</p>}
      </div>
      {actions && <div className="page__actions">{actions}</div>}
    </div>
  )
}
