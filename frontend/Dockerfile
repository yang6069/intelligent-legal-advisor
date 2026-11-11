# ==================================================================
# 智法通前端镜像 | 多阶段构建
# ------------------------------------------------------------------
# 阶段一（build）: node:20-alpine 执行 vite 构建 -> dist/
# 阶段二（serve）: nginx:alpine 托管静态资源 + /api 反向代理后端
# ==================================================================

# ---------- 阶段一：构建 ----------
FROM swr.cn-north-4.myhuaweicloud.com/ddn-k8s/docker.io/library/node:20-alpine AS build

WORKDIR /app

# 依赖层缓存（仅 package.json 变更时重装）
COPY package.json ./
# npmmirror 国内镜像源加速
RUN npm config set registry https://registry.npmmirror.com && npm install

# 源码与构建
COPY . .
RUN npm run build

# ---------- 阶段二：运行 ----------
FROM swr.cn-north-4.myhuaweicloud.com/ddn-k8s/docker.io/library/nginx:alpine

# Nginx 配置（SPA 路由回退 + /api 反代 + SSE 透传）
COPY nginx.conf /etc/nginx/conf.d/default.conf
# 构建产物
COPY --from=build /app/dist /usr/share/nginx/html

EXPOSE 80

# 显式使用 IPv4 环回地址，避免 busybox wget 将 localhost 解析为
# IPv6 (::1) 导致连接拒绝（nginx 默认仅监听 IPv4 0.0.0.0:80）
HEALTHCHECK --interval=30s --timeout=5s --retries=5 \
    CMD wget -qO- http://127.0.0.1/ >/dev/null || exit 1

CMD ["nginx", "-g", "daemon off;"]
