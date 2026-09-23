# py_screen_mirror

Screen mirroring + remote control via WiFi (1 jaringan) pakai Python.

- `target.py` → dijalankan di PC yang dishare layarnya / dikontrol
- `client.py` → dijalankan di PC yang melihat / mengontrol

## Install

```bash
pip install -r requirements.txt
```

## Pakai

1. Kedua PC konek ke WiFi yang sama.
2. Di PC target:
```bash
python target.py
```
Catat IP-nya, misal `192.168.1.5`.
3. Di PC client:
```bash
python client.py
```
Masukkan IP target itu.

Port: `5000` video, `5001` kontrol mouse/keyboard + clipboard teks dua arah.

Copy-paste: copy teks di salah satu PC, otomatis kesync — tinggal paste (Ctrl+V) di PC satunya. Khusus teks, maksimal ~100 KB.

Di client ada tombol toolbar:
- **Copy dari target** = block teks di layar target dulu, klik tombol (ngirim Ctrl+C ke target), tunggu ~1 detik, paste di PC sendiri.
- **Paste ke target** = kirim isi clipboard PC sendiri ke target + otomatis Ctrl+V di sana.
