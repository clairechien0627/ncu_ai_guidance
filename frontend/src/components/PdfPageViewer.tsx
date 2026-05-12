import { useEffect, useRef } from 'react'
import * as pdfjsLib from 'pdfjs-dist'

pdfjsLib.GlobalWorkerOptions.workerSrc = new URL(
  'pdfjs-dist/build/pdf.worker.min.mjs',
  import.meta.url,
).href

interface Props {
  url: string
  page: number
  onPageCount?: (n: number) => void
}

export default function PdfPageViewer({ url, page, onPageCount }: Props) {
  const containerRef = useRef<HTMLDivElement>(null)
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const pdfRef = useRef<pdfjsLib.PDFDocumentProxy | null>(null)
  const renderingRef = useRef(false)
  const pendingRef = useRef<{ page: number; w: number; h: number } | null>(null)

  const renderPage = (pdf: pdfjsLib.PDFDocumentProxy, pageNum: number, containerW: number, containerH: number) => {
    if (renderingRef.current) {
      pendingRef.current = { page: pageNum, w: containerW, h: containerH }
      return
    }
    renderingRef.current = true
    pdf.getPage(pageNum).then(pdfPage => {
      const canvas = canvasRef.current
      if (!canvas) { renderingRef.current = false; return }
      const dpr = window.devicePixelRatio || 1
      const natural = pdfPage.getViewport({ scale: 1 })
      const fitScale = Math.min(containerW / natural.width, containerH / natural.height)
      const renderScale = Math.max(fitScale, 1.8) * dpr
      const viewport = pdfPage.getViewport({ scale: renderScale })
      canvas.width = viewport.width
      canvas.height = viewport.height
      // Display at contain size regardless of render resolution
      const cssW = natural.width * fitScale
      const cssH = natural.height * fitScale
      canvas.style.width = `${cssW}px`
      canvas.style.height = `${cssH}px`
      const ctx = canvas.getContext('2d')!
      return pdfPage.render({ canvasContext: ctx, viewport, canvas }).promise
    }).then(() => {
      renderingRef.current = false
      if (pendingRef.current) {
        const { page: p, w, h } = pendingRef.current
        pendingRef.current = null
        renderPage(pdf, p, w, h)
      }
    }).catch(() => { renderingRef.current = false })
  }

  const triggerRender = (pageNum: number) => {
    const pdf = pdfRef.current
    const container = containerRef.current
    if (!pdf || !container) return
    renderPage(pdf, pageNum, container.clientWidth - 16, container.clientHeight - 24)
  }

  useEffect(() => {
    let cancelled = false
    pdfjsLib.getDocument({ url, cMapUrl: '/cmaps/', cMapPacked: true }).promise.then(pdf => {
      if (cancelled) return
      pdfRef.current = pdf
      onPageCount?.(pdf.numPages)
      triggerRender(page)
    }).catch(() => {})
    return () => { cancelled = true }
  }, [url])

  useEffect(() => { triggerRender(page) }, [page])

  useEffect(() => {
    const container = containerRef.current
    if (!container) return
    const ro = new ResizeObserver(() => triggerRender(page))
    ro.observe(container)
    return () => ro.disconnect()
  }, [page])

  return (
    <div ref={containerRef} style={{ flex: 1, overflow: 'auto', background: '#f3f4f6', display: 'flex', alignItems: 'center', justifyContent: 'center', padding: '12px 8px', minHeight: 0 }}>
      <canvas ref={canvasRef} style={{ boxShadow: '0 2px 12px rgba(0,0,0,0.15)', borderRadius: 2, display: 'block' }} />
    </div>
  )
}
