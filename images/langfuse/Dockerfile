FROM registry.access.redhat.com/ubi9/ubi-minimal:9.8 AS tls-tools
RUN microdnf install -y openssl && microdnf clean all

FROM docker.langfuse.com/langfuse/langfuse:4.76.0 AS web
COPY --from=tls-tools /usr/bin/openssl /usr/local/bin/openssl
RUN openssl version
USER 0
COPY secure-migrations.mjs /app/secure-migrations.mjs
RUN node /app/secure-migrations.mjs /app/packages/shared/clickhouse/scripts/up.sh
USER 1001
COPY nais-bootstrap.mjs /app/nais-bootstrap.mjs
ENTRYPOINT ["dumb-init", "--", "node", "/app/nais-bootstrap.mjs", "web"]
CMD ["node", "./web/server.js", "--keepAliveTimeout", "110000"]

FROM docker.langfuse.com/langfuse/langfuse-worker:4.76.0 AS worker
COPY --from=tls-tools /usr/bin/openssl /usr/local/bin/openssl
RUN openssl version
COPY nais-bootstrap.mjs /app/nais-bootstrap.mjs
ENTRYPOINT ["dumb-init", "--", "node", "/app/nais-bootstrap.mjs", "worker"]
CMD ["node", "worker/dist/index.js"]
