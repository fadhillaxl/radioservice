# Icom IC-9700 Docker Service (`rigctld` + ALSA + Web UI)

Layanan Docker berbasis **Ubuntu 24.04** untuk mengontrol transceiver **Icom IC-9700** (Hamlib model `3081`) di host Ubuntu Linux, dilengkapi auto-discovery perangkat USB UART (`cp210x` / `10c4:ea60`) dan USB Audio CODEC (`snd-usb-audio` / TI PCM2901 `08bb:2901`), serta **Web UI** untuk monitoring real-time dan update setting.

## Fitur Utama

- **Host Udev & Kernel Driver Setup (`setup-host.sh` & `99-icom-ic9700.rules`)**
  - Mengaktifkan modul kernel `cp210x` dan `snd-usb-audio` secara persisten.
  - Aturan udev untuk VID/PID `10c4:ea60` (Silicon Labs CP210x Dual UART) dan `08bb:2901` (TI PCM2901 USB Audio CODEC) dengan penonaktifan USB autosuspend.
- **Entrypoint Auto-Discovery (`entrypoint.sh`)**
  - Deteksi otomatis kartu audio ALSA CODEC dan pembuatan `/etc/asound.conf`.
  - Deteksi symlink serial Port A (`/dev/serial/by-id/*IC-9700*A-if00-port0`) dengan fallback ke `/dev/ttyUSB0`.
  - Tes baca frekuensi VFO (`rigctl -m 3081 -s 115200 f`) saat startup.
  - Menjalankan `rigctld` yang bind ke `0.0.0.0:4532`.
- **Web UI Monitoring & Update Setting (`webui/` di Port `8080`)**
  - Monitoring status `rigctld`, USB UART, ALSA Audio CODEC, frekuensi VFO, mode, bandwidth passband, VFO, PTT (RX/TX), S-Meter, dan SWR.
  - Kontrol frekuensi (step tuning & preset band 2m / 70cm / 23cm), mode, VFO, PTT, serta level RF Power, AF Gain, dan Squelch.
  - Update setting layanan (Port Serial, Baudrate, Model ID, Alamat CI-V, Bind Address/Port) dan restart `rigctld` langsung dari browser.

## Persiapan di Radio Icom IC-9700

Pada menu transceiver IC-9700 (`MENU > SET > Connectors > CI-V`):
- **CI-V USB Port**: `Unlink from [REMOTE]`
- **CI-V USB Baud Rate**: `115200`
- **CI-V Address**: `A2h`

## Cara Instalasi & Menjalankan

1. **Setup Host Ubuntu (Udev & Kernel Modules):**
   ```bash
   sudo ./setup-host.sh
   ```

2. **Jalankan Layanan dengan Docker Compose:**
   ```bash
   docker compose up -d --build
   ```

3. **Akses Layanan:**
   - **Web UI Monitoring & Settings:** `http://<IP-HOST>:8080`
   - **Hamlib `rigctld` TCP Server:** `<IP-HOST>:4532`

4. **Tes Koneksi `rigctld` via CLI:**
   ```bash
   echo "f" | nc -w 2 127.0.0.1 4532
   ```
