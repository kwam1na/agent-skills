# syntax=docker/dockerfile:1
# The portable hosted delivery service. Build it from the root of an extracted service bundle, or of a checkout:
#   docker build -f hosted/delivery-viewer/Dockerfile -t hosted-delivery-service .
# Run it as its non-root user on a read-only root filesystem, with its data on a local block volume at /data,
# behind a TLS-terminating proxy whose access logging is off or path-free:
#   docker run --read-only -v deliveries:/data -e PUBLISHERS_FILE=/run/secrets/publishers -p 127.0.0.1:8080:8080 hosted-delivery-service
# Both stages use this one base, pinned by digest: Node 24.22.2 on Debian bookworm slim, as an image index.
ARG NODE_IMAGE=node:24.22.2-bookworm-slim@sha256:01d566e8d94d8c619591d78652babfccd3c6992a3dffa9ed5ab39e11cf6c70e9

FROM ${NODE_IMAGE} AS build
WORKDIR /src
COPY hosted/delivery-viewer/package.json hosted/delivery-viewer/package-lock.json hosted/delivery-viewer/
RUN cd hosted/delivery-viewer && npm ci --ignore-scripts --no-audit --no-fund --engine-strict
COPY hosted/delivery-viewer/ hosted/delivery-viewer/
COPY skills/delivery-views/assets/ skills/delivery-views/assets/
COPY skills/delivery-views/scripts/render.py skills/delivery-views/scripts/render.py
COPY skills/delivery-views/references/hosted-snapshot.schema.json skills/delivery-views/references/hosted-snapshot.schema.json
COPY tests/fixtures/delivery-views/hosted/ tests/fixtures/delivery-views/hosted/
RUN cd hosted/delivery-viewer && npm run build:node

# The runtime holds only Node and the one built module: no npm install, no sources, no development dependencies.
FROM ${NODE_IMAGE}
ENV NODE_ENV=production HOST=0.0.0.0 PORT=8080 DATA_DIR=/data
COPY --from=build /src/hosted/delivery-viewer/build/node/server.mjs /app/server.mjs
RUN mkdir -m 0700 /data && chown node:node /data
USER node
VOLUME ["/data"]
EXPOSE 8080
ENTRYPOINT ["node", "/app/server.mjs"]
