interface Props {
  children: React.ReactNode
  empty?: string
  loading?: boolean
}

export function AdminTable({ children, empty = '沒有資料', loading }: Props) {
  return (
    <div className="adm-table-wrap">
      {loading ? (
        <div className="adm-table-empty">
          <div className="adm-spinner" style={{ margin: '0 auto 8px' }} />
          載入中…
        </div>
      ) : (
        <table className="adm-table">
          {children}
        </table>
      )}
      {!loading && (children as any)?.props?.children === undefined && (
        <div className="adm-table-empty">{empty}</div>
      )}
    </div>
  )
}

export function AdminTableEmpty({ message }: { message?: string }) {
  return (
    <tr>
      <td colSpan={99} className="adm-table-empty" style={{ border: 'none' }}>
        {message ?? '沒有資料'}
      </td>
    </tr>
  )
}
