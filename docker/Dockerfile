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
COPY docker/thermoctl_optionen.py /usr/local/bin/thermoctl_optionen.py
COPY docker/thermoctl_ingress.py /usr/local/bin/thermoctl_ingress.py
# Kein `USER thermoctl` hier: der Home-Assistant-Supervisor legt /data/options.json
# selbst an -- root:root, nur für root lesbar -- und der Container muss sie lesen
# können, bevor er überhaupt weiß, wie er sich konfigurieren soll. Das Abbild startet
# deshalb als root; `docker/entrypoint.sh` liest die Optionsdatei, sorgt dafür, dass
# /data (SQLite-Datenbank inbegriffen) für den Dienst beschreibbar ist, und gibt die
# Rechte an den unprivilegierten Benutzer `thermoctl` ab, bevor `alembic` oder der
# Dienst selbst laufen -- der läuft also weiterhin unprivilegiert, wie zuvor.
# Für den gewöhnlichen docker-compose-Betrieb ohne eigenes `user:` ändert das nichts
# am Ergebnis, nur den Zwischenschritt; ein Operator, der den Container heute explizit
# mit `user:` festnagelt (etwa dem bisherigen Standard, uid 10001), startet weiterhin
# direkt als dieser Benutzer, und der Rechteabgabe-Block in entrypoint.sh greift dann
# gar nicht erst.
RUN chmod +x /usr/local/bin/entrypoint.sh && mkdir -p /data && chown thermoctl /data
WORKDIR /app
VOLUME ["/data"]
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
  CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8000/healthz')"
ENTRYPOINT ["entrypoint.sh"]
