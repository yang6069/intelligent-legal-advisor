// ==================================================================
// 智法通前端 | Vite 构建配置
// ------------------------------------------------------------------
// 开发环境: /api 代理到本地后端 28000 端口（规避 CORS）
// 生产构建: 输出静态资源到 dist/（由 Nginx 容器托管）
// ==================================================================
import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  plugins: [vue()],
  server: {
    port: 5173,
    // 开发代理：前端 5173 -> 后端 28000
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:28000',
        changeOrigin: true,
        // EventSource(SSE) 支持：保持长连接、不缓冲、透传 content-type
        configure: (proxy) => {
          proxy.on('proxyRes', (proxyRes, req) => {
            if (proxyRes.headers['content-type']?.includes('text/event-stream')) {
              proxyRes.headers['cache-control'] = 'no-cache'
              proxyRes.headers['connection'] = 'keep-alive'
            }
          })
        },
      },
    },
  },
  build: {
    outDir: 'dist',
    // 大依赖拆分（echarts/element-plus 独立 chunk，提升缓存命中）
    rollupOptions: {
      output: {
        manualChunks: {
          vendor: ['vue', 'vue-router', 'pinia', 'axios'],
          ui: ['element-plus', '@element-plus/icons-vue'],
          charts: ['echarts'],
        },
      },
    },
  },
})
