# syntax=docker/dockerfile:1.10
# Next.js standalone build: the distroless runtime holds only node, server.js, the traced node_modules and the
# static assets (no shell, no package manager).
ARG NODE_VERSION=24

FROM node:${NODE_VERSION}-bookworm-slim AS deps
WORKDIR /app
COPY package.json package-lock.json ./
RUN --mount=type=cache,target=/root/.npm npm ci --no-audit --no-fund

FROM node:${NODE_VERSION}-bookworm-slim AS build
WORKDIR /app
ENV NEXT_TELEMETRY_DISABLED=1
# The public API URL is inlined into client code at build time; server-side calls read ATLAS_API_URL at runtime.
ARG NEXT_PUBLIC_ATLAS_API_URL=http://localhost:8000
ENV NEXT_PUBLIC_ATLAS_API_URL=${NEXT_PUBLIC_ATLAS_API_URL}
COPY --from=deps /app/node_modules ./node_modules
COPY . .
RUN npm run build

FROM gcr.io/distroless/nodejs${NODE_VERSION}-debian13:nonroot AS runtime
LABEL org.opencontainers.image.source="https://github.com/Manuelma4/eu-disclosure-atlas" \
      org.opencontainers.image.description="EU Disclosure Atlas website (Next.js standalone)" \
      org.opencontainers.image.licenses="Apache-2.0"
WORKDIR /app
ENV NODE_ENV=production NEXT_TELEMETRY_DISABLED=1 PORT=3000 HOSTNAME=0.0.0.0
COPY --from=build --chown=10001:10001 /app/.next/standalone ./
COPY --from=build --chown=10001:10001 /app/.next/static ./.next/static
USER 10001:10001
EXPOSE 3000
# The distroless entrypoint is node itself.
CMD ["server.js"]
