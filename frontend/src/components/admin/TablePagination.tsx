import { useEffect, useState } from 'react'
import { ChevronLeft, ChevronRight, ChevronsLeft, ChevronsRight } from 'lucide-react'

interface TablePaginationProps {
  page: number                      // 1-indexed
  pageCount: number
  pageSize: number
  total?: number                    // 顯示用，可選
  onPageChange: (page: number) => void
  onPageSizeChange?: (size: number) => void
  pageSizeOptions?: number[]
  showFirstLast?: boolean           // default true
}

const DEFAULT_SIZES = [10, 25, 50, 100]

export function TablePagination({
  page,
  pageCount,
  pageSize,
  total,
  onPageChange,
  onPageSizeChange,
  pageSizeOptions = DEFAULT_SIZES,
  showFirstLast = true,
}: TablePaginationProps) {
  const [inputVal, setInputVal] = useState(String(page))

  useEffect(() => { setInputVal(String(page)) }, [page])

  const commit = () => {
    const v = parseInt(inputVal)
    if (!isNaN(v) && v >= 1 && v <= pageCount) onPageChange(v)
    else setInputVal(String(page))
  }

  if (pageCount <= 0) return null

  return (
    <div className="adm-table-pagination">
      <span className="adm-tpag-total">
        {total != null ? `${total.toLocaleString()} rows` : `${pageCount} pages`}
      </span>

      <div className="adm-tpag-controls">
        {onPageSizeChange && (
          <div className="adm-tpag-group">
            <span className="adm-tpag-label">Rows per page</span>
            <select
              className="adm-tpag-select"
              value={pageSize}
              onChange={e => onPageSizeChange(Number(e.target.value))}
            >
              {pageSizeOptions.map(n => <option key={n} value={n}>{n}</option>)}
            </select>
          </div>
        )}

        <div className="adm-tpag-group">
          <span className="adm-tpag-label">Page</span>
          <input
            className="adm-tpag-input"
            type="number" min={1} max={pageCount}
            value={inputVal}
            onChange={e => setInputVal(e.target.value)}
            onBlur={commit}
            onKeyDown={e => e.key === 'Enter' && commit()}
          />
          <span className="adm-tpag-label">of {pageCount}</span>
        </div>

        <div className="adm-tpag-group">
          {showFirstLast && (
            <button className="adm-tpag-btn" title="First" disabled={page <= 1} onClick={() => onPageChange(1)}>
              <ChevronsLeft size={13} />
            </button>
          )}
          <button className="adm-tpag-btn" title="Prev" disabled={page <= 1} onClick={() => onPageChange(page - 1)}>
            <ChevronLeft size={13} />
          </button>
          <button className="adm-tpag-btn" title="Next" disabled={page >= pageCount} onClick={() => onPageChange(page + 1)}>
            <ChevronRight size={13} />
          </button>
          {showFirstLast && (
            <button className="adm-tpag-btn" title="Last" disabled={page >= pageCount} onClick={() => onPageChange(pageCount)}>
              <ChevronsRight size={13} />
            </button>
          )}
        </div>
      </div>
    </div>
  )
}
