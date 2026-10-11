import uuid, threading, shutil, traceback, webbrowser, time, copy, os
from pathlib import Path
from PIL import Image
from fastapi import FastAPI, UploadFile, File, Form, Body, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, Response
import uvicorn
import pipeline as P
import providers as PV
import paths

app = FastAPI(); JOBS = {}
CHAINS = {"all": ["detect", "ocr", "translate"], "everything": ["detect", "ocr", "translate", "inpaint", "export"]}

def mklog(j):
    def f(m):
        j["msg"] = m; j["log"].append(time.strftime("%H:%M:%S  ") + m); print(m)
    return f

@app.get("/")
def index(): return HTMLResponse((paths.RES / "static" / "index.html").read_text(encoding="utf-8"))

# ---- logo / favicon: drop logo.png (or .webp/.jpg/.svg) into static/ ; a built-in placeholder is used until then ----
FALLBACK_LOGO = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 120 80"><rect width="120" height="80" rx="14" fill="#0b101d"/>'
    '<path d="M10 12h56a8 8 0 0 1 8 8v24a8 8 0 0 1-8 8H34l-12 10v-10H10a8 8 0 0 1-8-8V20a8 8 0 0 1 8-8z" fill="#fff"/>'
    '<path d="M58 30h50a8 8 0 0 1 8 8v22a8 8 0 0 1-8 8H98v9L86 68H58a8 8 0 0 1-8-8V38a8 8 0 0 1 8-8z" fill="#ff5c8a"/>'
    '<text x="38" y="38" font-size="12" font-weight="700" text-anchor="middle" fill="#0b101d" font-family="Arial">DOUJIN</text>'
    '<text x="83" y="55" font-size="15" font-weight="700" text-anchor="middle" fill="#fff" font-family="Arial">\u540c\u4eba</text></svg>')

@app.get("/logo")
def logo():
    for ext, mt in ((".png", "image/png"), (".webp", "image/webp"), (".jpg", "image/jpeg"), (".jpeg", "image/jpeg"), (".svg", "image/svg+xml")):
        for d in (paths.APP / "static", paths.RES / "static"):  # a logo placed next to the exe overrides the bundled one
            p = d / ("logo" + ext)
            if p.exists(): return FileResponse(p, media_type=mt, headers={"Cache-Control": "no-cache"})
    return Response(FALLBACK_LOGO, media_type="image/svg+xml")

# ---- AI / API translators (API panel) ----
@app.get("/api/providers")
def prov_list(): return PV.listing()

@app.post("/api/providers/{pid}")
def prov_save(pid: str, body: dict = Body(...)):
    try: PV.update(pid, body)
    except KeyError: return {"error": "Unknown provider"}
    return PV.listing()

@app.post("/api/providers/{pid}/test")
def prov_test(pid: str):
    if pid not in PV.BY: return {"ok": False, "error": "Unknown provider"}
    return PV.test(pid)

@app.get("/api/providers/{pid}/models")
def prov_models(pid: str):
    try: return {"models": PV.models(pid)}
    except Exception as e: return {"error": str(e)[:300]}

@app.get("/favicon.ico")
def favicon():
    for d in (paths.APP / "static", paths.RES / "static"):
        if (d / "favicon.ico").exists(): return FileResponse(d / "favicon.ico", media_type="image/x-icon")
    return Response(status_code=404)

STATE = {"ping": 0.0}
@app.get("/api/ping")
def ping(): STATE["ping"] = time.time(); return {"app": "doujin", "frozen": paths.FROZEN}

@app.post("/api/quit")
def quit_app(): threading.Timer(.4, lambda: os._exit(0)).start(); return {"ok": True}

@app.get("/api/thumb/{jid}/{i}")
def thumb(jid: str, i: int):
    j = JOBS.get(jid); src = j and j["w"] / "pages" / f"{i:05d}.jpg"
    if not src or not src.exists(): raise HTTPException(404)
    tp = j["w"] / "thumbs" / f"{i:05d}.jpg"
    if not tp.exists(): tp.parent.mkdir(exist_ok=True); im = Image.open(src); im.thumbnail((120, 170)); im.convert("RGB").save(tp, quality=72)
    return FileResponse(tp, media_type="image/jpeg", headers={"Cache-Control": "max-age=86400"})

@app.get("/api/fonts")
def fonts(): return P.list_fonts_all()

# ---- 1. Load: extract pages only ----
def run_load(jid, source):
    j = JOBS[jid]; w = j["w"]; log = mklog(j)
    try:
        log("Loading pages…")
        pages = P.from_url(source, w, log) if isinstance(source, str) else P.get_pages(source, w)
        j["pages"] = P.new_pages(w); j["status"] = "idle"; log(f"{len(pages)} pages loaded. Next: Analyze.")
    except Exception as e: traceback.print_exc(); j["status"] = "error"; log("ERROR: " + str(e))

@app.post("/api/load")
def load(file: UploadFile = File(None), url: str = Form("")):
    jid = uuid.uuid4().hex[:8]; w = P.WORK / jid; w.mkdir(parents=True); name = "chapter"; source = url.strip()
    if file is not None and file.filename:
        name = Path(file.filename).stem; source = w / ("upload" + Path(file.filename).suffix.lower())
        with open(source, "wb") as f: shutil.copyfileobj(file.file, f)
    elif not source: return {"error": "Choose a file or enter a URL"}
    JOBS[jid] = dict(status="running", stage="load", msg="Queued", name=name, w=w, pages=[], out=None, log=[], total=0, done=0, cur=None, stop=False)
    threading.Thread(target=run_load, args=(jid, source), daemon=True).start()
    return {"id": jid}

# ---- 2-7. Stages (each runs on its own button; scope = all pages or chosen pages) ----
def run_stages(jid, names, o, idx):
    j = JOBS[jid]; log = mklog(j)
    try:
        for s in names:
            if j["stop"]: break
            j["stage"] = s
            if s == "export": P.stage_export(j, o, log)
            else: getattr(P, "stage_" + s)(j, idx, o, log)
        j["status"] = "idle"; log("Done: " + " → ".join(names) + (" (stopped)" if j["stop"] else ""))
    except Exception as e: traceback.print_exc(); j["status"] = "error"; log("ERROR: " + str(e))
    finally:
        for p in j["pages"]: p["run"] = ""

@app.post("/api/stage/{jid}/{name}")
def stage(jid: str, name: str, body: dict = Body(...)):
    j = JOBS.get(jid)
    if not j or not j["pages"]: return {"error": "Load a file first"}
    if j["status"] == "running": return {"error": "Another step is still running"}
    for p, q in zip(j["pages"], body.get("pages", [])): p["blocks"] = q["blocks"]  # take the user's edits
    n = len(j["pages"]); idx = [i for i in (body.get("scope") or range(n)) if 0 <= i < n]
    o = dict(sweep=bool(body.get("sweep")), src=body.get("src", "ja"), tgt=body.get("tgt", "en"), engine=body.get("engine", "online"),
             force=bool(body.get("force")), format=body.get("format", "cbz"), style=body.get("style") or {})
    names = CHAINS.get(name, [name])
    if body.get("then_export") and name == "all": names = CHAINS["everything"]
    j.update(status="running", stop=False)
    threading.Thread(target=run_stages, args=(jid, names, o, idx), daemon=True).start()
    return {"ok": True}

@app.post("/api/stop/{jid}")
def stop(jid: str): JOBS[jid]["stop"] = True; return {"ok": True}

def snap(j):
    for _ in range(5):
        try: return copy.deepcopy(j["pages"])
        except RuntimeError: time.sleep(.02)
    return None

@app.get("/api/job/{jid}")
def job(jid: str):
    j = JOBS[jid]
    return dict(status=j["status"], stage=j["stage"], msg=j["msg"], pages=snap(j), log=j["log"][-80:], total=j["total"], done=j["done"], cur=j["cur"], out=bool(j["out"]))

@app.get("/api/page/{jid}/{i}")
def page(jid: str, i: int): return FileResponse(P.WORK / jid / "pages" / f"{i:05d}.jpg")

@app.get("/api/out/{jid}/{i}")
def out(jid: str, i: int): return FileResponse(P.WORK / jid / "out" / f"{i:05d}.jpg", headers={"Cache-Control": "no-store"})

# ---- single-box tools used by the editor ----
@app.post("/api/box/{jid}/{action}")
def box(jid: str, action: str, body: dict = Body(...)):
    j = JOBS[jid]
    if j["status"] == "running": return {"error": "Wait for the running step to finish"}
    try:
        if action == "ocr": return {"text": P.one_box_ocr(j["w"], body["page"], body["box"], body["src"])}
        return {"text": P.one_box_translate(j["w"], body["page"], body["box"], body["text"], body["src"], body["tgt"], body["engine"])}
    except Exception as e: traceback.print_exc(); return {"error": str(e)}

@app.get("/api/download/{jid}")
def download(jid: str): p = Path(JOBS[jid]["out"]); return FileResponse(p, filename=p.name)

if __name__ == "__main__":
    threading.Timer(1.5, webbrowser.open, ["http://127.0.0.1:8000"]).start()
    uvicorn.run(app, host="127.0.0.1", port=8000)
