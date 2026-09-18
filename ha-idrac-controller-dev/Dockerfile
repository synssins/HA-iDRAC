FROM python:3.11-slim-bookworm

SHELL ["/bin/bash", "-o", "pipefail", "-c"]

# ipmitool kept for iDRAC7 fallback and Dell fan raw commands
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    ipmitool \
    bash \
    jq \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY app/requirements.txt ./
RUN python3 -m pip install --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

COPY app/ /app/

COPY run.sh /usr/local/bin/run.sh
RUN chmod +x /usr/local/bin/run.sh

CMD ["/usr/local/bin/run.sh"]
