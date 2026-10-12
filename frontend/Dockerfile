# ═══════════════════════════════════════════════════════════════
#  Frontend — Multi-stage: Node builder → nginx:alpine runtime
# ═══════════════════════════════════════════════════════════════

# ── Stage 1: Build ───────────────────────────────────────────
FROM node:20-slim AS builder

WORKDIR /app

# Browser-visible API origin is compiled into the static Next.js export.
ARG NEXT_PUBLIC_API_URL=http://localhost:8011
ENV NEXT_PUBLIC_API_URL=$NEXT_PUBLIC_API_URL
# Shows the "Sign in with Microsoft" button on /login.
ARG NEXT_PUBLIC_ENTRA_ENABLED=false
ENV NEXT_PUBLIC_ENTRA_ENABLED=$NEXT_PUBLIC_ENTRA_ENABLED
# Shows the "Sign in with Databricks" button on /login.
ARG NEXT_PUBLIC_DATABRICKS_MODE=false
ENV NEXT_PUBLIC_DATABRICKS_MODE=$NEXT_PUBLIC_DATABRICKS_MODE

COPY package*.json ./
RUN npm ci --legacy-peer-deps

COPY . .
RUN npm run build

# ── Stage 2: Runtime ─────────────────────────────────────────
FROM nginx:alpine

LABEL maintainer="DataOne"

# Install wget for healthcheck
RUN apk add --no-cache wget

# Remove default nginx static assets
RUN rm -rf /usr/share/nginx/html/*

# Copy built static export from builder
COPY --from=builder /app/out /usr/share/nginx/html

# Custom nginx config to handle Next.js client-side routing
RUN echo 'server { \
    listen 3000; \
    server_name localhost; \
    root /usr/share/nginx/html; \
    index index.html; \
    location / { \
        try_files $uri $uri.html $uri/ /index.html; \
    } \
    error_page 500 502 503 504 /50x.html; \
    location = /50x.html { \
        root /usr/share/nginx/html; \
    } \
}' > /etc/nginx/conf.d/default.conf

EXPOSE 3000

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD wget -qO- http://localhost:3000 || exit 1

CMD ["nginx", "-g", "daemon off;"]
