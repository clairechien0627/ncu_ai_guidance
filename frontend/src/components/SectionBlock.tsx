import type { ReactNode } from 'react'
import './SectionBlock.css'

export function SectionBlock({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div className="section-block">
      <h3 className="section-block-title">
        <span className="section-block-bar" />
        {title}
      </h3>
      <div className="section-block-body">{children}</div>
    </div>
  )
}
