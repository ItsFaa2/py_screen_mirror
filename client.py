"""
CLIENT - yang melihat & mengontrol
Jalankan file ini di PC yang mau mengontrol target.

Cara pakai:
  1. pip install -r requirements.txt
  2. python client.py
  3. Masukkan IP target (yang tampil di target.py), contoh: 192.168.1.5

Kontrol:
  - Gerak + klik mouse di gambar = gerak + klik di target
  - Scroll mouse = scroll di target
  - Ketik keyboard saat jendela gambar fokus = mengetik di target
"""
import socket
import struct
import threading
import json
import time
import io
import tkinter as tk

from PIL import Image, ImageTk

VIDEO_PORT = 5000
CONTROL_PORT = 5001


def recvall(sock, n):
    data = b""
    while len(data) < n:
        part = sock.recv(n - len(data))
        if not part:
            return None
        data += part
    return data


class MirrorClient:
    def __init__(self, host):
        self.host = host
        self.vsock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.vsock.settimeout(5)
        try:
            self.vsock.connect((host, VIDEO_PORT))
        except socket.gaierror:
            raise SystemExit(
                f"[CLIENT] '{host}' bukan IP/hostname yang valid.\n"
                "Contoh yang benar: 192.168.1.5 (lihat angka IP di target.py,\n"
                "BUKAN 5000/5001 karena itu nomor port)."
            )
        except (ConnectionRefusedError, TimeoutError, OSError) as e:
            raise SystemExit(
                f"[CLIENT] tidak bisa connect ke {host}:{VIDEO_PORT} ({e}).\n"
                "- Pastikan target.py SUDAH jalan di PC satunya\n"
                "- Satu WiFi, firewall allow Python, port 5000/5001 terbuka"
            )
        self.vsock.settimeout(None)
        self.csock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.csock.settimeout(5)
        try:
            self.csock.connect((host, CONTROL_PORT))
        except (ConnectionRefusedError, TimeoutError, OSError) as e:
            self.vsock.close()
            raise SystemExit(f"[CLIENT] port kontrol {CONTROL_PORT} gagal ({e}).")
        self.csock.settimeout(None)
        print(f"[CLIENT] connect ke {host} OK")

        self.latest_img = None
        self.lock = threading.Lock()
        self.running = True
        self.last_move = 0

        # --- GUI ---
        self.root = tk.Tk()
        self.root.title(f"Mirror - {host} (klik gambar untuk kontrol)")
        self.label = tk.Label(self.root, bg="black")
        self.label.pack(fill=tk.BOTH, expand=True)
        self.photo = None

        # mouse
        self.label.bind("<Motion>", self.on_move)
        self.label.bind("<ButtonPress-1>", lambda e: self.send_click("left", True))
        self.label.bind("<ButtonRelease-1>", lambda e: self.send_click("left", False))
        self.label.bind("<ButtonPress-3>", lambda e: self.send_click("right", True))
        self.label.bind("<ButtonRelease-3>", lambda e: self.send_click("right", False))
        self.label.bind("<ButtonPress-2>", lambda e: self.send_click("middle", True))
        self.label.bind("<ButtonRelease-2>", lambda e: self.send_click("middle", False))
        # scroll: Windows/Mac
        self.label.bind("<MouseWheel>", lambda e: self.send({"t": "scroll", "dy": int(e.delta / 120) * 120}))
        # scroll: Linux
        self.label.bind("<Button-4>", lambda e: self.send({"t": "scroll", "dy": 120}))
        self.label.bind("<Button-5>", lambda e: self.send({"t": "scroll", "dy": -120}))

        # keyboard (label harus fokus)
        self.label.focus_set()
        self.label.bind("<KeyPress>", lambda e: self.on_key(e, True))
        self.label.bind("<KeyRelease>", lambda e: self.on_key(e, False))
        self.root.protocol("WM_DELETE_WINDOW", self.close)

        threading.Thread(target=self.video_loop, daemon=True).start()
        self.root.after(30, self.refresh_gui)

    def send(self, msg):
        try:
            self.csock.sendall((json.dumps(msg) + "\n").encode("utf-8"))
        except Exception as e:
            print(f"[CLIENT] kirim gagal: {e}")

    def rel_pos(self, event):
        w = self.label.winfo_width() or 1
        h = self.label.winfo_height() or 1
        return max(0.0, min(1.0, event.x / w)), max(0.0, min(1.0, event.y / h))

    def on_move(self, event):
        now = time.time()
        if now - self.last_move < 0.03:  # throttle biar tidak banjir
            return
        self.last_move = now
        x, y = self.rel_pos(event)
        self.send({"t": "move", "x": x, "y": y})

    def send_click(self, button, pressed):
        self.send({"t": "click", "button": button, "pressed": pressed})

    def on_key(self, event, pressed):
        # keysym Tkinter mis: 'a', 'space', 'Return', 'BackSpace'...
        self.send({"t": "key", "key": event.keysym, "pressed": pressed})

    def video_loop(self):
        try:
            while self.running:
                raw_len = recvall(self.vsock, 4)
                if not raw_len:
                    print("[CLIENT] koneksi video putus")
                    break
                size = struct.unpack(">I", raw_len)[0]
                data = recvall(self.vsock, size)
                if not data:
                    break
                img = Image.open(io.BytesIO(data)).convert("RGB")
                with self.lock:
                    self.latest_img = img
        except Exception as e:
            print(f"[CLIENT] video error: {e}")
        finally:
            self.running = False

    def refresh_gui(self):
        if not self.running:
            try:
                self.root.destroy()
            except Exception:
                pass
            return
        with self.lock:
            img = self.latest_img
        if img is not None:
            # fit ke ukuran jendela biar tidak kepotong
            w = self.root.winfo_width() or img.width
            h = self.root.winfo_height() or img.height
            if w > 50 and h > 50:
                img2 = img.copy()
                img2.thumbnail((w, h))
                self.photo = ImageTk.PhotoImage(img2)
                self.label.config(image=self.photo)
        self.root.after(30, self.refresh_gui)

    def close(self):
        self.running = False
        try:
            self.vsock.close()
        except Exception:
            pass
        try:
            self.csock.close()
        except Exception:
            pass
        try:
            self.root.destroy()
        except Exception:
            pass

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    import re
    print("=== CLIENT (yang mengontrol) ===")
    print("PENTING: yang dimasukkan = IP target, contoh 192.168.1.5")
    print("(lihat tulisan 'IP PC ini:' di target.py. BUKAN 5000/5001.)")
    print("Tes di 1 PC yang sama? pakai: 127.0.0.1\n")
    while True:
        host = input("IP target: ").strip()
        if not host:
            print("IP kosong, coba lagi.")
            continue
        if re.fullmatch(r"\d{1,5}", host):
            print(f"'{host}' itu nomor port, bukan IP. Masukkan IP kayak 192.168.1.5")
            continue
        try:
            socket.getaddrinfo(host, VIDEO_PORT)
        except socket.gaierror:
            print(f"'{host}' tidak dikenal. Contoh valid: 192.168.1.5")
            continue
        break
    app = MirrorClient(host)
    app.run()
