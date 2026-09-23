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
  - Copy-paste teks dua arah: copy di salah satu PC, paste di satunya
  - Menu Aksi = Copy/Paste + Fullscreen (F11, Esc buat keluar)
"""
import socket
import struct
import threading
import json
import time
import io
import tkinter as tk

from PIL import Image, ImageTk

try:
    import pyperclip
    HAVE_CLIPBOARD = True
except ImportError:
    pyperclip = None
    HAVE_CLIPBOARD = False

VIDEO_PORT = 5000
CONTROL_PORT = 5001
CLIP_MAX = 100_000


def map_to_relative(ex, ey, label_w, label_h, img_w, img_h):
    """Petakan posisi mouse di label ke koordinat relatif 0..1 di gambar.
    Gambar di-tampilkan thumbnail (letterbox, center), jadi harus
    dikurangi offset sisi yang kosong."""
    if not img_w or not img_h:
        img_w, img_h = label_w, label_h
    ox = (label_w - img_w) / 2
    oy = (label_h - img_h) / 2
    rx = (ex - ox) / img_w
    ry = (ey - oy) / img_h
    return max(0.0, min(1.0, rx)), max(0.0, min(1.0, ry))


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
        self.send_lock = threading.Lock()
        self.running = True
        self.last_move = 0

        # baseline clipboard biar isi lama tidak langsung terkirim pas connect
        self.clip_last_sent = None
        self.clip_last_recv = None
        if HAVE_CLIPBOARD:
            try:
                self.clip_last_sent = pyperclip.paste()
            except Exception:
                pass
        else:
            print("[CLIPBOARD] pyperclip tidak ada, sync copy-paste nonaktif.")

        # --- GUI ---
        self.root = tk.Tk()
        self.base_title = f"Mirror - {host} (klik gambar untuk kontrol)"
        self.root.title(self.base_title)

        # menu tipis (biar area gambar full, tanpa toolbar)
        menubar = tk.Menu(self.root)
        aksi = tk.Menu(menubar, tearoff=0)
        aksi.add_command(label="Copy dari target", command=self.on_copy_btn)
        aksi.add_command(label="Paste ke target", command=self.on_paste_btn)
        aksi.add_separator()
        aksi.add_command(label="Fullscreen (F11)", command=self.toggle_fullscreen)
        aksi.add_command(label="Keluar", command=self.close)
        menubar.add_cascade(label="Aksi", menu=aksi)
        self.root.config(menu=menubar)
        self.root.bind("<F11>", lambda e: self.toggle_fullscreen())
        self.root.bind("<Escape>", lambda e: self.set_fullscreen(False))
        self.is_fullscreen = False

        self.label = tk.Label(self.root, bg="black")
        self.label.pack(fill=tk.BOTH, expand=True)
        self.photo = None
        self.disp_w = None  # ukuran gambar yg tampil (habis thumbnail)
        self.disp_h = None

        # mouse (klik selalu bawa posisi biar tidak ketinggalan kursor)
        self.label.bind("<Motion>", self.on_move)
        self.label.bind("<ButtonPress-1>", lambda e: self.on_press("left", e))
        self.label.bind("<ButtonRelease-1>", lambda e: self.send_click("left", False))
        self.label.bind("<ButtonPress-3>", lambda e: self.on_press("right", e))
        self.label.bind("<ButtonRelease-3>", lambda e: self.send_click("right", False))
        self.label.bind("<ButtonPress-2>", lambda e: self.on_press("middle", e))
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
        threading.Thread(target=self.control_reader, daemon=True).start()
        threading.Thread(target=self.clipboard_poller, daemon=True).start()
        self.root.after(30, self.refresh_gui)

    def send(self, msg):
        try:
            with self.send_lock:
                self.csock.sendall((json.dumps(msg) + "\n").encode("utf-8"))
        except Exception as e:
            print(f"[CLIENT] kirim gagal: {e}")

    def control_reader(self):
        """Baca pesan dari target (clipboard target -> clipboard client)."""
        buf = b""
        try:
            while self.running:
                chunk = self.csock.recv(4096)
                if not chunk:
                    print("[CLIENT] koneksi kontrol putus")
                    break
                buf += chunk
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    if not line.strip():
                        continue
                    try:
                        msg = json.loads(line.decode("utf-8", "ignore"))
                    except Exception:
                        continue
                    if msg.get("t") == "clipboard" and HAVE_CLIPBOARD:
                        text = str(msg.get("text", ""))
                        if len(text) > CLIP_MAX:
                            continue
                        self.clip_last_recv = text
                        try:
                            pyperclip.copy(text)
                            print(f"[CLIPBOARD] terima {len(text)} char dari target")
                        except Exception as e:
                            print(f"[CLIPBOARD] copy gagal: {e}")
        except Exception:
            pass
        finally:
            self.running = False

    def clipboard_poller(self):
        """Kirim clipboard client ke target kalau berubah."""
        if not HAVE_CLIPBOARD:
            return
        while self.running:
            time.sleep(1.0)
            try:
                cur = pyperclip.paste()
            except Exception:
                continue
            if not isinstance(cur, str):
                continue
            if cur == self.clip_last_sent or cur == self.clip_last_recv:
                continue
            if len(cur) > CLIP_MAX:
                self.clip_last_sent = cur
                continue
            self.send({"t": "clipboard", "text": cur})
            self.clip_last_sent = cur
            print(f"[CLIPBOARD] kirim {len(cur)} char ke target")

    def rel_pos(self, event):
        w = self.label.winfo_width() or 1
        h = self.label.winfo_height() or 1
        return map_to_relative(event.x, event.y, w, h, self.disp_w, self.disp_h)

    def on_move(self, event):
        now = time.time()
        if now - self.last_move < 0.02:  # ~50 update/detik
            return
        self.last_move = now
        x, y = self.rel_pos(event)
        self.send({"t": "move", "x": x, "y": y})

    def on_press(self, button, event):
        # kirim posisi dulu baru klik (biar klik pas di titik tekan)
        x, y = self.rel_pos(event)
        self.send({"t": "move", "x": x, "y": y})
        self.send({"t": "click", "button": button, "pressed": True})
        self.last_move = time.time()

    def send_click(self, button, pressed):
        self.send({"t": "click", "button": button, "pressed": pressed})

    def on_key(self, event, pressed):
        # keysym Tkinter mis: 'a', 'space', 'Return', 'Control_L'...
        self.send({"t": "key", "key": event.keysym, "pressed": pressed})

    def set_status(self, text):
        try:
            self.root.title(f"{self.base_title} — {text}")
        except Exception:
            pass

    def set_fullscreen(self, on):
        self.is_fullscreen = on
        try:
            self.root.attributes("-fullscreen", on)
        except Exception:
            pass

    def toggle_fullscreen(self):
        self.set_fullscreen(not self.is_fullscreen)

    def on_copy_btn(self):
        """Pencet Ctrl+C di target (block teks dulu di sana),
        hasilnya kesync otomatis ke clipboard PC ini."""
        self.send({"t": "hotkey", "keys": ["ctrl", "c"]})
        self.set_status("copy dikirim, tunggu ~1 detik lalu paste lokal")
        print("[CLIENT] tombol copy: ctrl+c dikirim ke target")
        self.label.focus_set()

    def on_paste_btn(self):
        """Kirim isi clipboard PC ini ke target lalu pencet Ctrl+V di sana."""
        if not HAVE_CLIPBOARD:
            self.set_status("pyperclip tidak ada")
            return
        try:
            text = pyperclip.paste()
        except Exception as e:
            self.set_status(f"clipboard gagal: {e}")
            return
        if not isinstance(text, str) or not text:
            self.set_status("clipboard kosong")
            return
        if len(text) > CLIP_MAX:
            self.set_status("teks kebesaran (>100KB)")
            return
        self.clip_last_sent = text  # biar poller tidak kirim dobel
        self.send({"t": "clipboard", "text": text})
        self.send({"t": "hotkey", "keys": ["ctrl", "v"]})
        self.set_status(f"paste {len(text)} char ke target")
        print(f"[CLIENT] tombol paste: {len(text)} char + ctrl+v ke target")
        self.label.focus_set()

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
                self.disp_w, self.disp_h = img2.size  # buat mapping mouse
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
