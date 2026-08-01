# ---- build stage ----
FROM node:22-alpine AS builder

WORKDIR /app

# 依存だけ先に入れてレイヤキャッシュを効かせる。
COPY package.json package-lock.json* ./
# lock ファイルがあれば npm ci、無ければ npm install。
RUN if [ -f package-lock.json ]; then npm ci; else npm install; fi

COPY tsconfig.json vite.config.ts index.html ./
COPY src ./src

# 型チェック込みでビルドする (tsc --noEmit && vite build)。
RUN npm run build

# ---- runtime stage ----
FROM nginx:1.27-alpine

RUN rm -f /etc/nginx/conf.d/default.conf
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=builder /app/dist /usr/share/nginx/html

EXPOSE 80

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=5 \
    CMD wget -q -O /dev/null http://127.0.0.1/ || exit 1
