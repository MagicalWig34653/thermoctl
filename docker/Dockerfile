FROM python:3.14-slim AS build
WORKDIR /build
COPY pyproject.toml LICENSE ./
COPY thermoctl ./thermoctl
RUN pip install --no-cache-dir --prefix=/install .

FROM python:3.14-slim
LABEL org.opencontainers.image.licenses="AGPL-3.0-only"
RUN useradd --system --create-home --uid 10001 thermoctl
COPY --from=build /install /usr/local
COPY migrations /app/migrations
COPY alembic.ini /app/alembic.ini
COPY docker/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh && mkdir -p /data && chown thermoctl /data
WORKDIR /app
USER thermoctl
VOLUME ["/data"]
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
  CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8000/healthz')"
ENTRYPOINT ["entrypoint.sh"]
