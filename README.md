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

Port: `5000` video, `5001` kontrol mouse/keyboard.
