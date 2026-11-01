ARG BUILD_FROM
FROM ${BUILD_FROM}

# Python 3.11 di bookworm basta (requisito 3.11+); tzdata per l'orario settimanale in ora italiana
RUN apt-get update \
 && apt-get install -y --no-install-recommends python3 python3-venv tzdata \
 && rm -rf /var/lib/apt/lists/*
ENV TZ=Europe/Rome

# il codice, mai config.yaml, cache o sessione (preparato da scripts/ha_addon.sh). Playwright serve solo per le
# richieste HTTP con i cookie della sessione: niente browser da scaricare.
COPY app /app
WORKDIR /app
RUN python3 -m venv .venv && .venv/bin/pip install --no-cache-dir "playwright>=1.47" "PyYAML>=6.0" "anthropic>=1.0"

COPY run.sh /run.sh
RUN chmod a+x /run.sh
CMD [ "/run.sh" ]
