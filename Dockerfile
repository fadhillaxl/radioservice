FROM ubuntu:24.04

LABEL maintainer="radioservice"
LABEL description="Hamlib rigctld, ALSA USB & Web UI service for Icom IC-9700 (Model 3081)"

ENV DEBIAN_FRONTEND=noninteractive

# Instal paket yang dibutuhkan:
# - libhamlib-utils (rigctl, rigctld)
# - libhamlib4      (shared library Hamlib)
# - alsa-utils      (aplay, arecord, amixer, alsactl)
# - usbutils        (lsusb)
# - udev            (udevadm)
# - netcat-openbsd  (nc untuk TCP healthcheck)
# - python3         (server Web UI & supervisor rigctld tanpa dependensi eksternal)
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        libhamlib-utils \
        libhamlib4 \
        alsa-utils \
        usbutils \
        udev \
        netcat-openbsd \
        python3 \
        ca-certificates && \
    rm -rf /var/lib/apt/lists/* && \
    mkdir -p /config /opt/webui

# Konfigurasi default untuk Icom IC-9700
ENV RIG_MODEL=3081 \
    RIG_BAUD=115200 \
    RIG_PORT="" \
    CIV_ADDR="0xA2" \
    RIGCTLD_BIND=0.0.0.0 \
    RIGCTLD_PORT=4532 \
    RIGCTLD_EXTRA_ARGS="" \
    ENABLE_WEBUI=true \
    WEB_BIND=0.0.0.0 \
    WEB_PORT=8080 \
    STRICT_VFO_CHECK=false

COPY webui/ /opt/webui/
COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh /opt/webui/app.py

EXPOSE 4532 8080

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
