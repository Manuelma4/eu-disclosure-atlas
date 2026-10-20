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
# Browser-facing URLs are inlined into client code at build time (defaults match the code's local defaults);
# server-side calls read ATLAS_API_URL at runtime.
ARG NEXT_PUBLIC_ATLAS_API_URL=http://127.0.0.1:8000
ARG NEXT_PUBLIC_ATLAS_STREAM_URL=http://127.0.0.1:8000
ARG NEXT_PUBLIC_ATLAS_ALERTS_URL=http://localhost:8091
ARG NEXT_PUBLIC_ATLAS_OIDC_ISSUER=http://localhost:8180/realms/atlas
ARG NEXT_PUBLIC_ATLAS_OIDC_CLIENT_ID=atlas-web
ENV NEXT_PUBLIC_ATLAS_API_URL=${NEXT_PUBLIC_ATLAS_API_URL} \
    NEXT_PUBLIC_ATLAS_STREAM_URL=${NEXT_PUBLIC_ATLAS_STREAM_URL} \
    NEXT_PUBLIC_ATLAS_ALERTS_URL=${NEXT_PUBLIC_ATLAS_ALERTS_URL} \
    NEXT_PUBLIC_ATLAS_OIDC_ISSUER=${NEXT_PUBLIC_ATLAS_OIDC_ISSUER} \
    NEXT_PUBLIC_ATLAS_OIDC_CLIENT_ID=${NEXT_PUBLIC_ATLAS_OIDC_CLIENT_ID}
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
