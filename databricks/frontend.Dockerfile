# ═══════════════════════════════════════════════════════════════
#  DataOne Frontend — Databricks Lakehouse App Build
#  Next.js static export optimized for Databricks runtime
# ═══════════════════════════════════════════════════════════════

# ── Stage 1: Dependencies ────────────────────────────────────
FROM node:20-slim AS deps

WORKDIR /app

# Copy package files
COPY frontend/package*.json ./

# Install dependencies with legacy peer deps for compatibility
RUN npm ci --legacy-peer-deps --production=false

# ── Stage 2: Builder ─────────────────────────────────────────
FROM node:20-slim AS builder

WORKDIR /app

# Copy dependencies from deps stage
COPY --from=deps /app/node_modules ./node_modules

# Copy source code
COPY frontend/ .

# Build arguments for Databricks environment
# These will be injected at runtime via environment variables in app.yml
ARG NEXT_PUBLIC_API_URL=__DATABRICKS_APP_URL__/api/v1
ARG NEXT_PUBLIC_WS_URL=__DATABRICKS_APP_URL__/ws
ARG NEXT_PUBLIC_APP_NAME=DataOne
ARG NEXT_PUBLIC_DATABRICKS_MODE=true
ARG NEXT_PUBLIC_ENTRA_ENABLED=false

ENV NEXT_PUBLIC_API_URL=$NEXT_PUBLIC_API_URL \
    NEXT_PUBLIC_WS_URL=$NEXT_PUBLIC_WS_URL \
    NEXT_PUBLIC_APP_NAME=$NEXT_PUBLIC_APP_NAME \
    NEXT_PUBLIC_DATABRICKS_MODE=$NEXT_PUBLIC_DATABRICKS_MODE \
    NEXT_PUBLIC_ENTRA_ENABLED=$NEXT_PUBLIC_ENTRA_ENABLED \
    NODE_ENV=production

# Build Next.js static export
RUN npm run build

# Verify build output
RUN ls -la /app/out && \
    echo "Build completed successfully"

# ── Stage 3: Runtime ─────────────────────────────────────────
FROM nginx:alpine

LABEL maintainer="Veltris Technologies" \
      version="1.0.0" \
      description="DataOne Frontend for Databricks Lakehouse Apps"

# Install runtime utilities
RUN apk add --no-cache \
    wget \
    curl \
    ca-certificates \
    && rm -rf /var/cache/apk/*

# Create non-root user (matching Databricks conventions)
RUN addgroup -g 1000 appuser && \
    adduser -D -u 1000 -G appuser appuser

# Remove default nginx static assets
RUN rm -rf /usr/share/nginx/html/*

# Copy built static export from builder
COPY --from=builder /app/out /usr/share/nginx/html

# Create nginx configuration for Next.js routing + Databricks proxy
RUN echo 'server { \
    listen 3000; \
    server_name localhost; \
    root /usr/share/nginx/html; \
    index index.html; \
    \
    # Enable gzip compression \
    gzip on; \
    gzip_vary on; \
    gzip_min_length 1024; \
    gzip_types text/plain text/css text/xml text/javascript application/javascript application/json application/xml+rss; \
    \
    # Security headers \
    add_header X-Frame-Options "SAMEORIGIN" always; \
    add_header X-Content-Type-Options "nosniff" always; \
    add_header X-XSS-Protection "1; mode=block" always; \
    add_header Referrer-Policy "strict-origin-when-cross-origin" always; \
    \
    # Handle Next.js client-side routing \
    location / { \
        try_files $uri $uri.html $uri/ /index.html; \
    } \
    \
    # Proxy API requests to backend \
    location /api/ { \
        proxy_pass http://backend:8000/api/; \
        proxy_http_version 1.1; \
        proxy_set_header Upgrade $http_upgrade; \
        proxy_set_header Connection "upgrade"; \
        proxy_set_header Host $host; \
        proxy_set_header X-Real-IP $remote_addr; \
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for; \
        proxy_set_header X-Forwarded-Proto $scheme; \
        proxy_read_timeout 300s; \
        proxy_connect_timeout 75s; \
    } \
    \
    # WebSocket support for real-time features \
    location /ws { \
        proxy_pass http://backend:8000/ws; \
        proxy_http_version 1.1; \
        proxy_set_header Upgrade $http_upgrade; \
        proxy_set_header Connection "upgrade"; \
        proxy_set_header Host $host; \
        proxy_set_header X-Real-IP $remote_addr; \
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for; \
    } \
    \
    # Cache static assets \
    location ~* \\.(?:css|js|jpg|jpeg|gif|png|ico|svg|woff|woff2|ttf|eot)$ { \
        expires 1y; \
        add_header Cache-Control "public, immutable"; \
    } \
    \
    # Health check endpoint \
    location /health { \
        access_log off; \
        return 200 "healthy\\n"; \
        add_header Content-Type text/plain; \
    } \
    \
    error_page 500 502 503 504 /50x.html; \
    location = /50x.html { \
        root /usr/share/nginx/html; \
    } \
}' > /etc/nginx/conf.d/default.conf

# Set proper permissions
RUN chown -R appuser:appuser /usr/share/nginx/html && \
    chown -R appuser:appuser /var/cache/nginx && \
    chown -R appuser:appuser /var/log/nginx && \
    touch /var/run/nginx.pid && \
    chown appuser:appuser /var/run/nginx.pid

# Switch to non-root user
USER appuser

EXPOSE 3000

# Health check
HEALTHCHECK --interval=15s --timeout=10s --retries=3 --start-period=10s \
    CMD wget -qO- http://localhost:3000/health || exit 1

CMD ["nginx", "-g", "daemon off;"]
