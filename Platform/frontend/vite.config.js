import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

const apiTarget = `http://127.0.0.1:${process.env.EPFO_API_PORT || '8792'}`

export default defineConfig({
  plugins: [react()],
  server: {
    host: '127.0.0.1',
    port: 5201,
    proxy: {
      '/api': apiTarget,
      '/health': apiTarget,
      '/docs': apiTarget,
      '/swagger': apiTarget,
      '/openapi.json': apiTarget,
    },
  },
})
