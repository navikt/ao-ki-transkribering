FROM clickhouse/clickhouse-server:26.4

USER root
COPY nais.xml /etc/clickhouse-server/nais.xml
COPY nais-users.xml /etc/clickhouse-server/nais-users.xml
USER 1069:1069
ENTRYPOINT ["clickhouse-server", "--config-file=/etc/clickhouse-server/nais.xml"]
