# Optionale Matter-Bridge fuer thermoctl. Eigener Container, kein Teil des thermoctl-Abbilds.
# Node 22.x erfuellt die engines von Matter.js 0.18.0 (20.19 bis <21 oder >=22.13).
FROM node:22-bookworm-slim AS deps
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --omit=dev --no-audit --no-fund

FROM node:22-bookworm-slim
LABEL org.opencontainers.image.licenses="AGPL-3.0-only" \
      org.opencontainers.image.description="Optionale Matter-Bridge fuer thermoctl (Apple Home, Google Home)"
WORKDIR /app
# node:sqlite (Matter.js-Speicher) ist in Node 22 noch als experimentell markiert; die Warnung
# wuerde bei jedem Start im Log stehen und jeden Healthcheck-Aufruf begleiten.
ENV NODE_ENV=production \
    NODE_OPTIONS=--disable-warning=ExperimentalWarning \
    MATTER_BRIDGE_STORAGE_DIR=/data
COPY --from=deps /app/node_modules ./node_modules
COPY package.json ./
COPY *.mjs ./
# Unprivilegierter Benutzer "node" (uid 1000) des Basisabbilds. Das Speicher-Volume muss
# ihm gehoeren; es enthaelt die Fabric-Schluessel der Bridge.
RUN mkdir -p /data && chown node:node /data && chmod 700 /data
USER node
VOLUME /data
# Matter-Standardport (TCP und UDP); mDNS braucht UDP 5353 im selben Netz (README: Host-Netz).
EXPOSE 5540/udp 5540/tcp
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD ["node", "/app/healthcheck.mjs"]
CMD ["node", "/app/main.mjs"]
