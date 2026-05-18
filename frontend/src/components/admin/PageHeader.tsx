import { Link } from 'react-router-dom'

interface Crumb { label: string; to?: string }

interface Props {
  title: string
  subtitle?: string
  crumbs?: Crumb[]
  actions?: React.ReactNode
}

export function PageHeader({ title, subtitle, crumbs, actions }: Props) {
  return (
    <div className="adm-page-header">
      <div className="adm-page-header-left">
        {crumbs && crumbs.length > 0 && (
          <div className="adm-breadcrumb">
            {crumbs.map((c, i) => (
              <span key={i} style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                {i > 0 && <span className="adm-breadcrumb-sep">/</span>}
                {c.to ? <Link to={c.to}>{c.label}</Link> : <span>{c.label}</span>}
              </span>
            ))}
          </div>
        )}
        <h1 className="adm-page-header-title">{title}</h1>
        {subtitle && <span className="adm-page-header-sub">{subtitle}</span>}
      </div>
      {actions && <div className="adm-page-header-actions">{actions}</div>}
    </div>
  )
}
