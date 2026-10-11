"""Doujin Comic Translator: entry point of the Windows .exe (also: python launcher.py).
Starts the local server, opens it in its own app window (Edge / Chrome --app, else your default browser) and quits with the window."""
import os, sys, socket, threading, time, subprocess, webbrowser, urllib.request, json, multiprocessing
from pathlib import Path
import paths

def log_to_file():
    d = paths.APP / "logs"; d.mkdir(parents=True, exist_ok=True)
    f = open(d / "app.log", "a", buffering=1, encoding="utf-8", errors="replace")
    if paths.FROZEN or sys.stdout is None or sys.stderr is None: sys.stdout = sys.stderr = f  # a windowed .exe has no console
    print("\n=== start", time.strftime("%Y-%m-%d %H:%M:%S"), "===")

def is_ours(port):
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/ping", timeout=1.5) as r: return json.loads(r.read()).get("app") == "doujin"
    except Exception: return False

def free_port(start=8000):
    for p in range(start, start + 50):
        with socket.socket() as s:
            s.settimeout(.2)
            if s.connect_ex(("127.0.0.1", p)) != 0: return p
    raise RuntimeError("No free port between 8000 and 8049")

def find_browser():
    for env in ("ProgramFiles(x86)", "ProgramFiles", "LocalAppData"):
        b = os.environ.get(env)
        for rel in (r"Microsoft\Edge\Application\msedge.exe", r"Google\Chrome\Application\chrome.exe"):
            if b and (Path(b) / rel).exists(): return str(Path(b) / rel)

def open_window(url):
    if os.environ.get("DOUJIN_NO_WINDOW"): return None
    exe = find_browser()
    if exe:  # own profile folder = own process: it ends when the window is closed, and the page gets its own taskbar entry
        try: return subprocess.Popen([exe, f"--app={url}", f"--user-data-dir={paths.APP / 'browser-profile'}", "--no-first-run", "--no-default-browser-check", "--window-size=1500,940"])
        except Exception as e: print("app window failed:", e)
    webbrowser.open(url); return None

def main():
    log_to_file()
    if is_ours(8000): open_window("http://127.0.0.1:8000"); return  # already running: just show it again
    port = free_port(8000); url = f"http://127.0.0.1:{port}"
    import uvicorn, app as A
    server = uvicorn.Server(uvicorn.Config(A.app, host="127.0.0.1", port=port, log_level="warning", log_config=None))
    t = threading.Thread(target=server.run, daemon=True); t.start()
    for _ in range(600):
        if server.started or not t.is_alive(): break
        time.sleep(.1)
    t0 = time.time(); proc = open_window(url)
    while t.is_alive():
        time.sleep(1)
        if proc is not None and proc.poll() is not None:
            if time.time() - t0 < 6: proc = None  # the browser handed over to an already running instance: fall back to the page heartbeat
            else: break  # the app window was closed
        if proc is None and A.STATE["ping"] and time.time() - A.STATE["ping"] > 300: break  # tab closed (background tabs ping slowly)
    print("closing"); os._exit(0)

if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()
