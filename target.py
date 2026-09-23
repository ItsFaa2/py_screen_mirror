"""
TARGET - yang dishare layarnya & dikontrol
Jalankan file ini di PC/laptop yang layarnya mau ditampilkan.

Cara pakai:
  1. pip install -r requirements.txt
  2. python target.py
  3. Catat IP yang muncul (contoh 192.168.1.5)
  4. Jalankan client.py di PC lain yang satu WiFi, masukkan IP itu

Port:
  5000 = video (target -> client)
  5001 = kontrol mouse/keyboard (client -> target)
       + clipboard teks dua arah (copy di satu PC, paste di satunya)
"""
import socket
import struct
import threading
import json
import time
import io

import mss
from PIL import Image
import pyautogui

try:
    import pyperclip
    HAVE_CLIPBOARD = True
except ImportError:
    pyperclip = None
    HAVE_CLIPBOARD = False
    print("[CLIPBOARD] pyperclip tidak ada, sync copy-paste nonaktif.")

VIDEO_PORT = 5000
CONTROL_PORT = 5001
FPS = 15
JPEG_QUALITY = 60
MAX_WIDTH = 1280  # resize agar ringan di WiFi
CLIP_MAX = 100_000  # teks > ini tidak disync

pyautogui.FAILSAFE = False

# koneksi kontrol aktif (buat kirim clipboard target -> client)
_clip_conn = None
_clip_lock = threading.Lock()
_clip_last_sent = None
_clip_last_recv = None


def get_lan_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except Exception:
        return socket.gethostbyname(socket.gethostname())
    finally:
        s.close()


def video_server():
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("0.0.0.0", VIDEO_PORT))
    srv.listen(1)
    print(f"[VIDEO] listening di port {VIDEO_PORT} ...")
    screen_w, screen_h = pyautogui.size()

    while True:
        conn, addr = srv.accept()
        print(f"[VIDEO] client connect: {addr}")
        try:
            with mss.MSS() as sct:
                monitor = sct.monitors[1]  # layar utama
                delay = 1.0 / FPS
                while True:
                    t0 = time.time()
                    shot = sct.grab(monitor)
                    img = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")

                    # kecilkan biar enteng
                    if img.width > MAX_WIDTH:
                        ratio = MAX_WIDTH / img.width
                        img = img.resize(
                            (MAX_WIDTH, int(img.height * ratio)), Image.BILINEAR
                        )

                    buf = io.BytesIO()
                    img.save(buf, format="JPEG", quality=JPEG_QUALITY)
                    data = buf.getvalue()

                    conn.sendall(struct.pack(">I", len(data)) + data)

                    dt = time.time() - t0
                    if dt < delay:
                        time.sleep(delay - dt)
        except (ConnectionResetError, BrokenPipeError):
            print("[VIDEO] client disconnect, nunggu client baru...")
        except Exception as e:
            print(f"[VIDEO] error: {e}")
        finally:
            try:
                conn.close()
            except Exception:
                pass


def _norm_key(key):
    """Normalisasi nama tombol Tkinter -> nama pyautogui.
    Contoh: 'Control_L' -> 'ctrl', 'Return' -> 'enter', 'a' -> 'a'."""
    k = str(key).lower()
    for suffix in ("_l", "_r"):
        if k.endswith(suffix):
            k = k[: -len(suffix)]
    alias = {
        "space": "space", "enter": "enter", "return": "enter",
        "backspace": "backspace", "tab": "tab", "escape": "esc",
        "esc": "esc", "shift": "shift", "ctrl": "ctrl",
        "control": "ctrl", "alt": "alt", "up": "up",
        "down": "down", "left": "left", "right": "right",
        "delete": "delete", "home": "home", "end": "end",
        "prior": "pgup", "next": "pgdn", "page_up": "pgup",
        "page_down": "pgdn", "caps_lock": "capslock",
        "win": "win", "super": "win", "menu": "apps",
        "insert": "insert", "pause": "pause",
        "f1": "f1", "f2": "f2", "f3": "f3", "f4": "f4",
        "f5": "f5", "f6": "f6", "f7": "f7", "f8": "f8",
        "f9": "f9", "f10": "f10", "f11": "f11", "f12": "f12",
    }
    return alias.get(k, k if len(k) > 1 else str(key))


def do_control(msg):
    """Eksekusi perintah kontrol dari client. x,y = relatif 0..1"""
    screen_w, screen_h = pyautogui.size()
    t = msg.get("t")

    if t == "move":
        x = int(msg.get("x", 0) * screen_w)
        y = int(msg.get("y", 0) * screen_h)
        pyautogui.moveTo(max(0, min(x, screen_w - 1)), max(0, min(y, screen_h - 1)))

    elif t == "click":
        btn = msg.get("button", "left")
        pressed = msg.get("pressed", True)
        if pressed:
            pyautogui.mouseDown(button=btn)
        else:
            pyautogui.mouseUp(button=btn)

    elif t == "scroll":
        pyautogui.scroll(int(msg.get("dy", 0)))

    elif t == "key":
        key = str(msg.get("key", ""))
        pressed = msg.get("pressed", True)
        k = _norm_key(key)
        try:
            if pressed:
                pyautogui.keyDown(k)
            else:
                pyautogui.keyUp(k)
        except Exception as e:
            print(f"[CONTROL] key gagal '{key}': {e}")

    elif t == "hotkey":
        # combo langsung, misal ["ctrl","c"] (dipakai tombol Copy/Paste)
        keys = [str(k) for k in msg.get("keys", [])][:4]
        try:
            pyautogui.hotkey(*keys)
            print(f"[CONTROL] hotkey {'+'.join(keys)}")
        except Exception as e:
            print(f"[CONTROL] hotkey gagal {keys}: {e}")

    elif t == "clipboard":
        # teks copy dari client -> tempel ke clipboard target
        global _clip_last_recv
        text = str(msg.get("text", ""))
        if len(text) > CLIP_MAX:
            print("[CLIPBOARD] teks dari client kebesaran, skip")
            return
        _clip_last_recv = text
        if HAVE_CLIPBOARD:
            try:
                pyperclip.copy(text)
                print(f"[CLIPBOARD] terima {len(text)} char dari client")
            except Exception as e:
                print(f"[CLIPBOARD] copy gagal: {e}")


def clipboard_poller():
    """Cek clipboard target tiap 1 detik, kirim ke client kalau berubah."""
    global _clip_last_sent
    if not HAVE_CLIPBOARD:
        return
    try:
        _clip_last_sent = pyperclip.paste()
    except Exception:
        _clip_last_sent = None
    while True:
        time.sleep(1.0)
        conn = _clip_conn
        if conn is None:
            continue
        try:
            cur = pyperclip.paste()
        except Exception:
            continue
        if not isinstance(cur, str):
            continue
        if cur == _clip_last_sent or cur == _clip_last_recv:
            continue
        if len(cur) > CLIP_MAX:
            _clip_last_sent = cur  # jangan spam, anggap sudah diproses
            continue
        try:
            with _clip_lock:
                conn.sendall((json.dumps({"t": "clipboard", "text": cur}) + "\n").encode("utf-8"))
            _clip_last_sent = cur
            print(f"[CLIPBOARD] kirim {len(cur)} char ke client")
        except Exception:
            pass  # koneksi putus, poller coba lagi nanti


def control_server():
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("0.0.0.0", CONTROL_PORT))
    srv.listen(1)
    print(f"[CONTROL] listening di port {CONTROL_PORT} ...")

    while True:
        conn, addr = srv.accept()
        print(f"[CONTROL] client connect: {addr}")
        global _clip_conn
        _clip_conn = conn
        buf = b""
        try:
            while True:
                chunk = conn.recv(4096)
                if not chunk:
                    break
                buf += chunk
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    if not line.strip():
                        continue
                    try:
                        do_control(json.loads(line.decode("utf-8", "ignore")))
                    except Exception as e:
                        print(f"[CONTROL] pesan jelek: {e}")
        except Exception as e:
            print(f"[CONTROL] error: {e}")
        finally:
            print("[CONTROL] client disconnect")
            if _clip_conn is conn:
                _clip_conn = None
            try:
                conn.close()
            except Exception:
                pass


if __name__ == "__main__":
    print("=== TARGET (yang dikontrol) ===")
    print(f"IP PC ini: {get_lan_ip()}")
    print("Kasih IP ini ke client.py di PC satunya (harus 1 WiFi).")
    print("Tekan Ctrl+C untuk berhenti.\n")
    threading.Thread(target=video_server, daemon=True).start()
    threading.Thread(target=control_server, daemon=True).start()
    threading.Thread(target=clipboard_poller, daemon=True).start()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nBerhenti.")
