#!/usr/bin/env bash
set -euo pipefail

RIG_MODEL="${RIG_MODEL:-3081}"
RIG_BAUD="${RIG_BAUD:-115200}"
RIG_PORT="${RIG_PORT:-}"
RIGCTLD_BIND="${RIGCTLD_BIND:-0.0.0.0}"
RIGCTLD_PORT="${RIGCTLD_PORT:-4532}"
RIGCTLD_EXTRA_ARGS="${RIGCTLD_EXTRA_ARGS:-}"
ENABLE_WEBUI="${ENABLE_WEBUI:-true}"
WEB_BIND="${WEB_BIND:-0.0.0.0}"
WEB_PORT="${WEB_PORT:-8080}"
STRICT_VFO_CHECK="${STRICT_VFO_CHECK:-false}"

log()  { echo "[$(date -Iseconds)] [INFO]  $*"; }
warn() { echo "[$(date -Iseconds)] [WARN]  $*" >&2; }
err()  { echo "[$(date -Iseconds)] [ERROR] $*" >&2; }

log "=== Memulai Layanan Kontrol Icom IC-9700 (Hamlib Model ${RIG_MODEL}) ==="

# ------------------------------------------------------------------------------
# 1. Verifikasi Perangkat USB (VID:PID 10c4:ea60 & 08bb:2901)
# ------------------------------------------------------------------------------
if command -v lsusb >/dev/null 2>&1; then
    if lsusb -d 10c4:ea60 >/dev/null 2>&1; then
        log "USB UART CP210x terdeteksi: $(lsusb -d 10c4:ea60)"
    else
        warn "USB UART CP210x (10c4:ea60) tidak terlihat di lsusb."
    fi

    if lsusb -d 08bb:2901 >/dev/null 2>&1; then
        log "USB Audio CODEC TI PCM2901 terdeteksi: $(lsusb -d 08bb:2901)"
    else
        warn "USB Audio CODEC TI PCM2901 (08bb:2901) tidak terlihat di lsusb."
    fi
fi

# ------------------------------------------------------------------------------
# 2. Auto-Discovery Kartu Audio ALSA CODEC (TI PCM2901)
# ------------------------------------------------------------------------------
log "Mendeteksi kartu audio ALSA USB Audio CODEC..."
ALSA_CARD_NUM=""
ALSA_CARD_ID=""

if [[ -r /proc/asound/cards ]]; then
    while IFS= read -r line; do
        if [[ "$line" =~ ^[[:space:]]*([0-9]+)[[:space:]]+\[([^]]+)\]:.*(USB-Audio|CODEC) ]]; then
            ALSA_CARD_NUM="${BASH_REMATCH[1]}"
            ALSA_CARD_ID="${BASH_REMATCH[2]}"
            break
        fi
    done < /proc/asound/cards
fi

if [[ -z "${ALSA_CARD_NUM}" ]] && command -v aplay >/dev/null 2>&1; then
    ALSA_LINE="$(aplay -l 2>/dev/null | grep -iE 'CODEC|USB Audio' | head -n 1 || true)"
    if [[ "${ALSA_LINE}" =~ ^card[[:space:]]+([0-9]+):[[:space:]]+([^[:space:]]+) ]]; then
        ALSA_CARD_NUM="${BASH_REMATCH[1]}"
        ALSA_CARD_ID="${BASH_REMATCH[2]%:}"
    fi
fi

if [[ -n "${ALSA_CARD_NUM}" ]]; then
    export ALSA_CARD="${ALSA_CARD_ID}"
    export IC9700_ALSA_DEVICE="plughw:${ALSA_CARD_NUM},0"
    log "Kartu audio ALSA CODEC ditemukan: Card #${ALSA_CARD_NUM} (${ALSA_CARD_ID}) -> ${IC9700_ALSA_DEVICE}"

    cat >/etc/asound.conf <<EOF
pcm.!default {
    type plug
    slave.pcm "hw:${ALSA_CARD_NUM},0"
}
ctl.!default {
    type hw
    card ${ALSA_CARD_NUM}
}
EOF
else
    warn "Kartu audio ALSA USB Audio CODEC tidak ditemukan! Pastikan modul snd-usb-audio aktif di host."
fi

# ------------------------------------------------------------------------------
# 3. Auto-Discovery Port Serial Port A (/dev/serial/by-id/*IC-9700*A-if00-port0)
#    dengan fallback ke /dev/ttyUSB0
# ------------------------------------------------------------------------------
SERIAL_PORT=""

if [[ -n "${RIG_PORT}" && -e "${RIG_PORT}" ]]; then
    SERIAL_PORT="${RIG_PORT}"
    log "Menggunakan RIG_PORT dari environment: ${SERIAL_PORT}"
else
    shopt -s nullglob
    BY_ID_MATCHES=(/dev/serial/by-id/*IC-9700*A-if00-port0)
    shopt -u nullglob

    if (( ${#BY_ID_MATCHES[@]} > 0 )); then
        SERIAL_PORT="${BY_ID_MATCHES[0]}"
        REAL_TTY="$(readlink -f "${SERIAL_PORT}" || echo "${SERIAL_PORT}")"
        log "Port A ditemukan via symlink by-id: ${SERIAL_PORT} (-> ${REAL_TTY})"
    elif [[ -e /dev/icom-ic9700-cat ]]; then
        SERIAL_PORT="/dev/icom-ic9700-cat"
        REAL_TTY="$(readlink -f "${SERIAL_PORT}" || echo "${SERIAL_PORT}")"
        log "Port A ditemukan via udev symlink: ${SERIAL_PORT} (-> ${REAL_TTY})"
    elif [[ -c /dev/ttyUSB0 ]]; then
        SERIAL_PORT="/dev/ttyUSB0"
        warn "Symlink /dev/serial/by-id/*IC-9700*A-if00-port0 tidak ditemukan. Menggunakan fallback: ${SERIAL_PORT}"
    else
        warn "Tidak ada port serial IC-9700 (/dev/serial/by-id/*IC-9700*A-if00-port0 maupun /dev/ttyUSB0) yang ditemukan saat startup."
        if [[ "${STRICT_VFO_CHECK}" == "true" || "${ENABLE_WEBUI}" != "true" ]]; then
            err "Menghentikan container karena port serial tidak ditemukan."
            exit 1
        fi
    fi
fi

# ------------------------------------------------------------------------------
# 4. Tes Baca Frekuensi VFO (rigctl f) pada Baudrate 115200
# ------------------------------------------------------------------------------
if [[ -n "${SERIAL_PORT}" && -c "${SERIAL_PORT}" ]]; then
    log "Melakukan tes baca frekuensi VFO: rigctl -m ${RIG_MODEL} -r ${SERIAL_PORT} -s ${RIG_BAUD} f"
    VFO_FREQ=""
    if VFO_FREQ="$(rigctl -m "${RIG_MODEL}" -r "${SERIAL_PORT}" -s "${RIG_BAUD}" f 2>&1)"; then
        VFO_FREQ_TRIMMED="$(echo "${VFO_FREQ}" | tr -d '[:space:]')"
        if [[ "${VFO_FREQ_TRIMMED}" =~ ^[0-9]+$ ]]; then
            log "Tes VFO berhasil! Frekuensi saat ini: ${VFO_FREQ_TRIMMED} Hz"
        else
            warn "rigctl keluar dengan kode 0 tetapi respons tidak standar: ${VFO_FREQ}"
        fi
    else
        warn "Gagal membaca frekuensi VFO dari IC-9700 (output: ${VFO_FREQ})."
        warn "Pastikan pengaturan menu IC-9700:"
        warn "  - MENU > SET > Connectors > CI-V > CI-V USB Port      : Unlink from [REMOTE]"
        warn "  - MENU > SET > Connectors > CI-V > CI-V USB Baud Rate : 115200"
        warn "  - MENU > SET > Connectors > CI-V > CI-V Address       : A2h"
        if [[ "${STRICT_VFO_CHECK}" == "true" || "${ENABLE_WEBUI}" != "true" ]]; then
            exit 1
        fi
    fi
fi

# ------------------------------------------------------------------------------
# 5. Jalankan Web UI Supervisor (atau langsung rigctld jika ENABLE_WEBUI=false)
# ------------------------------------------------------------------------------
if [[ "${ENABLE_WEBUI}" == "true" ]]; then
    log "Menjalankan Web UI (http://${WEB_BIND}:${WEB_PORT}) & rigctld (${RIGCTLD_BIND}:${RIGCTLD_PORT})..."
    exec python3 /opt/webui/app.py
else
    log "Menjalankan rigctld pada ${RIGCTLD_BIND}:${RIGCTLD_PORT}..."
    # shellcheck disable=SC2086
    exec rigctld \
        -m "${RIG_MODEL}" \
        -r "${SERIAL_PORT}" \
        -s "${RIG_BAUD}" \
        -T "${RIGCTLD_BIND}" \
        -t "${RIGCTLD_PORT}" \
        ${RIGCTLD_EXTRA_ARGS}
fi
