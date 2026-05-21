import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

export default defineConfig({
  plugins: [tailwindcss(), react()],
  resolve: {
    dedupe: ['react', 'react-dom', 'react-router-dom'],
  },
  build: {
    rollupOptions: {
      output: {
        manualChunks(id) {
          if (!id.includes('node_modules')) return

          if (id.includes('pdfjs-dist')) return 'pdf-vendor'

          if (id.includes('react-dom') || id.includes('react/') || id.includes('scheduler')) {
            return 'react-vendor'
          }

          if (
            id.includes('react-markdown') ||
            id.includes('remark-') ||
            id.includes('rehype-') ||
            id.includes('highlight.js') ||
            id.includes('lowlight') ||
            id.includes('mdast-util-') ||
            id.includes('micromark') ||
            id.includes('hast-util-') ||
            id.includes('unist-util-') ||
            id.includes('unified') ||
            id.includes('vfile')
          ) {
            return 'markdown-vendor'
          }

          if (id.includes('lucide-react')) return 'icons-vendor'

          if (id.includes('axios')) return 'net-vendor'
        },
      },
    },
  },
  server: {
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8200',
        changeOrigin: true,
      },
    },
  },
})
