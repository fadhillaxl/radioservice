#!/usr/bin/env bash
set -euo pipefail

# Skrip Setup Host Ubuntu untuk Icom IC-9700
# Jalankan dengan: sudo ./setup-host.sh

if [[ "${EUID}" -ne 0 ]]; then
    echo "[ERROR] Jalankan skrip ini sebagai root (sudo ./setup-host.sh)" >&2
    exit 1
fi

echo "[INFO] 1. Mengaktifkan modul kernel cp210x dan snd-usb-audio..."
modprobe cp210x
modprobe snd-usb-audio

cat >/etc/modules-load.d/icom-ic9700.conf <<'EOF'
# Kernel modules untuk Icom IC-9700
cp210x
snd-usb-audio
EOF
echo "[OK] Modul kernel dimuat dan disimpan di /etc/modules-load.d/icom-ic9700.conf"

echo "[INFO] 2. Memasang aturan udev /etc/udev/rules.d/99-icom-ic9700.rules..."
install -m 0644 99-icom-ic9700.rules /etc/udev/rules.d/99-icom-ic9700.rules

echo "[INFO] 3. Memuat ulang aturan udev..."
udevadm control --reload-rules
udevadm trigger

if [[ -n "${SUDO_USER:-}" ]]; then
    echo "[INFO] 4. Menambahkan user '${SUDO_USER}' ke grup dialout dan audio..."
    usermod -aG dialout,audio "${SUDO_USER}"
fi

echo "[OK] Setup host selesai. Silakan cabut-pasang kabel USB Icom IC-9700 jika diperlukan."
