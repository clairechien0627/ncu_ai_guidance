import { Children } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import remarkMath from 'remark-math'
import rehypeHighlight from 'rehype-highlight'
import rehypeKatex from 'rehype-katex'
import 'highlight.js/styles/github.css'
import 'katex/dist/katex.min.css'

interface Props {
  content: string
}

// Normalize math delimiters: ( ... ) → $...$  and  [ ... ] → $$...$$
// The AI sometimes outputs LaTeX with ( ) instead of $ $
function normalizeMath(text: string): string {
  return text
    .replace(/\\\[(.+?)\\\]/gs, '$$$$\n$1\n$$$$')
    .replace(/\\\((.+?)\\\)/gs, '$$$1$$')
    .replace(/(?<!\$)\(\s*(\\[a-zA-Z{].*?)\s*\)(?!\$)/gs, '$$$1$$')
}

export default function MarkdownRenderer({ content }: Props) {
  return (
    <div className="markdown-body">
      <ReactMarkdown
        remarkPlugins={[remarkGfm, remarkMath]}
        rehypePlugins={[rehypeHighlight, rehypeKatex]}
        components={{
          p: ({ children }) => {
            const isEmpty = Children.toArray(children).every(
              (child) => typeof child === 'string' && child.trim() === ''
            )
            if (isEmpty) return null
            return <p>{children}</p>
          },
        }}
      >
        {normalizeMath(content)}
      </ReactMarkdown>
    </div>
  )
}
