import os, re, shutil, subprocess, zipfile, time, urllib.request
from pathlib import Path
from urllib.parse import urljoin
import numpy as np, cv2, torch, requests
import torch.nn.functional as F
from PIL import Image, ImageDraw, ImageFont
import providers as PV

import paths
BASE = paths.APP
MODELS, FONTS, WORK = BASE / "models", BASE / "fonts", BASE / "work"
for d in (MODELS, FONTS, WORK): d.mkdir(parents=True, exist_ok=True)
os.environ["HF_HOME"] = str(MODELS / "hf"); os.environ["TORCH_HOME"] = str(MODELS / "torch")
DEV = "cuda" if torch.cuda.is_available() else "cpu"
IMG = (".jpg", ".jpeg", ".png", ".webp", ".bmp")
nat = lambda s: [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", str(s))]

def tool(*names):
    for n in names:
        if shutil.which(n): return shutil.which(n)
    for d in (r"C:\Program Files\7-Zip", r"C:\Program Files\WinRAR", r"C:\Program Files (x86)\WinRAR"):
        for n in names:
            if (Path(d) / (n + ".exe")).exists(): return str(Path(d) / (n + ".exe"))

# ---------- input: cbz/zip/rar/cbr/7z/pdf/images/folder/URL -> numbered page jpgs ----------
def get_pages(src, w):
    for s in ("ex", "pages"): shutil.rmtree(w / s, ignore_errors=True)
    d = w / "ex"; d.mkdir(parents=True); (w / "pages").mkdir()
    e = src.suffix.lower().lstrip(".")
    if src.is_dir(): shutil.copytree(src, d, dirs_exist_ok=True)
    elif e == "pdf":
        import pymupdf
        for k, p in enumerate(pymupdf.open(src)): p.get_pixmap(dpi=150).save(d / f"{k:05d}.png")
    elif e in ("zip", "cbz"): zipfile.ZipFile(src).extractall(d)
    elif e in ("rar", "cbr", "7z", "cb7"):
        z = tool("7z", "7za"); assert z, "Install 7-Zip to open rar/7z files"
        subprocess.run([z, "x", "-y", f"-o{d}", str(src)], capture_output=True)
    elif "." + e in IMG: shutil.copy(src, d)
    found = sorted((Path(r) / n for r, _, ns in os.walk(d) for n in ns if n.lower().endswith(IMG)), key=nat)
    out = []
    for p in found:
        im = Image.open(p).convert("RGB")
        if im.width < 200: continue  # skip icons/logos
        dst = w / "pages" / f"{len(out):05d}.jpg"; im.save(dst, quality=95); out.append(dst)
    assert out, "No pages found in the input"
    return out

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
_IMGX = r"(?:jpe?g|png|webp|avif|gif|bmp)"
_SKIP = re.compile(r"(logo|avatar|icon|sprite|banner|emoji|favicon|loading|placeholder|spinner|/ads?/|gravatar|badge|thumb)", re.I)
_ARCH = (".cbz", ".zip", ".cbr", ".rar", ".7z", ".cb7", ".pdf")

def _best_src(v):
    """'a.jpg 300w, b.jpg 700w' -> the widest one; a plain URL is returned as is."""
    if "," not in v and " " not in v.strip(): return v.strip()
    best, bw = None, -1
    for m in re.finditer(r"(\S+?)(?:\s+(\d+(?:\.\d+)?)[wx])?\s*(?:,\s|,$|$)", v.strip()):
        wv = float(m.group(2) or 0)
        if wv >= bw: best, bw = m.group(1), wv
    return best or v.strip()

def _page_images(html, base):
    """Image URLs in reading order: <img> (lazy-load attributes, srcset, noscript), then URLs hidden in scripts / JSON."""
    from html.parser import HTMLParser
    out = []
    class H(HTMLParser):
        def handle_starttag(s, tag, a):
            if tag != "img": return  # <picture><source> only repeats the <img> fallback in another format
            a = dict(a)
            for k in ("data-src", "data-original", "data-lazy-src", "data-lazy", "data-url", "data-echo", "data-cfsrc", "data-srcset", "srcset", "src"):
                v = (a.get(k) or "").strip()
                if v and not v.startswith("data:"): out.append(urljoin(base, _best_src(v))); break
    try: H(convert_charrefs=True).feed(html)
    except Exception: pass
    dom = list(dict.fromkeys(out)); good = [u for u in dom if not _SKIP.search(u)] or dom
    if len(good) < 3:  # pages loaded by JavaScript are usually listed in a script as a JSON array
        txt = html.replace("\\/", "/").replace("\\u002F", "/").replace("&amp;", "&"); extra = []
        for m in re.finditer(r"https?://[^\s\"'<>\\)]+?\." + _IMGX + r"(?:\?[^\s\"'<>\\)]*)?", txt, re.I): extra.append(m.group(0))
        for m in re.finditer(r"[\"'](/[^\"'\s<>]+?\." + _IMGX + r"(?:\?[^\"'\s<>]*)?)[\"']", txt, re.I): extra.append(urljoin(base, m.group(1)))
        good = list(dict.fromkeys(good + [u for u in extra if not _SKIP.search(u)]))
    # chapter pages sit in one CDN folder: keep the biggest folder when it clearly dominates
    from urllib.parse import urlparse
    grp = {}
    for u in good: q = urlparse(u); grp.setdefault((q.netloc, q.path.rsplit("/", 1)[0]), []).append(u)
    if grp:
        top = max(grp.values(), key=len)
        if len(top) >= 3 and len(top) >= .5 * len(good): good = top
    return good

def _why(r):
    t = (r.text[:3000] if "text" in r.headers.get("Content-Type", "text") else "").lower()
    if r.status_code in (403, 503, 429) and any(k in t or k in str(r.headers).lower() for k in ("cloudflare", "just a moment", "cf-chl", "captcha", "ddos")):
        return ("The site blocked the download (Cloudflare / bot protection). Open the chapter in your browser, save the pages with a "
                "downloader (e.g. gallery-dl) or the site's own download button, then load the folder / cbz here.")
    return {404: "Page not found (HTTP 404). Check the link.", 401: "Login required (HTTP 401).", 403: "Access denied (HTTP 403): the site refuses automatic downloads."}.get(r.status_code, f"The site answered HTTP {r.status_code}.")

def _gallery_dl(url, folder, log):
    """Last resort: the gallery-dl library knows many manga / doujin sites. Optional (pip install gallery-dl)."""
    try: from gallery_dl import config, job
    except Exception: return False
    try:
        log("Trying gallery-dl…"); config.clear(); config.set((), "base-directory", str(folder)); config.set(("extractor",), "directory", ["x"])
        config.set(("extractor",), "filename", "{num:>05}.{extension}"); config.set(("extractor",), "user-agent", UA)
        job.DownloadJob(url).run(); return True
    except Exception as e: log(f"gallery-dl could not open this link ({str(e)[:80]})"); return False

def from_url(url, w, log=print):
    from io import BytesIO
    from concurrent.futures import ThreadPoolExecutor
    url = url.strip()
    if not re.match(r"https?://", url, re.I): url = "https://" + url
    s = requests.Session(); s.headers.update({"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9", "Accept": "text/html,application/xhtml+xml,image/avif,image/webp,*/*;q=0.8"})
    dl = w / "dl"; shutil.rmtree(dl, ignore_errors=True); dl.mkdir(parents=True)
    try: r = s.get(url, timeout=30, stream=True)
    except requests.RequestException as e: raise RuntimeError(f"Could not reach the site ({type(e).__name__}). Check the link and your connection.")
    path = url.split("?")[0].lower(); ct = r.headers.get("Content-Type", "").lower()
    if r.status_code >= 400: raise RuntimeError(_why(r))
    if path.endswith(_ARCH) or any(k in ct for k in ("zip", "pdf", "rar", "7z", "x-cbz")) or (("octet-stream" in ct) and not path.endswith(("/", ".html", ".php"))):
        ext = next((e for e in _ARCH if path.endswith(e)), ".cbz" if "zip" in ct else ".pdf" if "pdf" in ct else ".cbz"); f = w / ("download" + ext)
        log("Downloading file…")
        with open(f, "wb") as o:
            for ch in r.iter_content(1 << 20): o.write(ch)
        return get_pages(f, w)
    if ct.startswith("image/") or path.endswith(IMG):
        (dl / "00000.jpg").write_bytes(r.content); return get_pages(dl, w)
    r.encoding = r.encoding or r.apparent_encoding; html = r.text
    urls = _page_images(html, r.url or url); log(f"Found {len(urls)} image links")
    def fetch(u):
        for k in range(3):
            try:
                g = s.get(u, headers={"Referer": url, "Accept": "image/avif,image/webp,image/*,*/*;q=0.8"}, timeout=40)
                if g.status_code == 200 and g.content: return g.content
                if g.status_code not in (429, 500, 502, 503): break
            except requests.RequestException: pass
            time.sleep(1.5 * (k + 1))
    imgs = []
    if urls:
        with ThreadPoolExecutor(6) as ex:
            for i, c in enumerate(ex.map(fetch, urls)):
                if c:
                    try: im = Image.open(BytesIO(c)); im.load(); imgs.append((im.convert("RGB") if im.mode not in ("RGB", "L") else im, im.size))
                    except Exception: pass
                if (i + 1) % 10 == 0: log(f"Downloaded {i + 1}/{len(urls)}")
    big = [x for x in imgs if x[1][0] >= 300] or imgs  # drop thumbnails / icons / ad strips
    if len(big) < 2 and _gallery_dl(url, dl, log): return get_pages(dl, w)
    if not big:
        raise RuntimeError("No comic pages found at this link. The site probably loads pages with JavaScript or needs a login. "
                           "Save the chapter from your browser (or with gallery-dl) and load the folder / cbz instead." if urls else
                           "No images found at this link. If it is a reader page that loads pages with JavaScript, save the chapter from your browser and load the folder / cbz instead.")
    for i, (im, _) in enumerate(big): im.save(dl / f"{i:05d}.jpg", quality=95)
    log(f"Downloaded {len(big)} pages")
    return get_pages(dl, w)

# ---------- detection + OCR ----------
_m = {}
def yolo():
    if "y" not in _m:
        try:
            from ultralytics import YOLO
            from huggingface_hub import hf_hub_download
            _m["y"] = YOLO(hf_hub_download("ogkalu/comic-text-segmenter-yolov8m", "comic-text-segmenter.pt"))
        except Exception as e:
            print("YOLO detector unavailable, using EasyOCR detector:", e); _m["y"] = None
    return _m["y"]

def easy(lang):
    import easyocr
    L = {"ja": ["ja", "en"], "ko": ["ko", "en"], "zh": ["ch_sim", "en"], "es": ["es", "en"], "en": ["en"]}[lang]
    if tuple(L) not in _m: _m[tuple(L)] = easyocr.Reader(L, gpu=DEV == "cuda", model_storage_directory=str(MODELS / "easyocr"))
    return _m[tuple(L)]

def merge(bs, k):
    bs = [list(b) for b in bs]
    def near(a, b):
        g = k * min(min(a[2]-a[0], a[3]-a[1]), min(b[2]-b[0], b[3]-b[1]))
        return a[0]-g < b[2] and b[0]-g < a[2] and a[1]-g < b[3] and b[1]-g < a[3]
    ch = True
    while ch:
        ch = False
        for i in range(len(bs)):
            for j in range(i + 1, len(bs)):
                if near(bs[i], bs[j]):
                    a, b = bs[i], bs.pop(j); bs[i] = [min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3])]; ch = True; break
            if ch: break
    return bs

def detect(tile):
    y = yolo()
    if y is not None:
        r = y(tile, verbose=False, conf=.3, device=0 if DEV == "cuda" else "cpu")[0]
        return merge(r.boxes.xyxy.cpu().numpy().tolist(), 0)
    h, _ = easy("en").detect(tile)
    return merge([[b[0], b[2], b[1], b[3]] for b in h[0]], .8)

def read(crop, lang):
    if lang == "ja":
        if "mocr" not in _m:
            from manga_ocr import MangaOcr; _m["mocr"] = MangaOcr()
        return _m["mocr"](Image.fromarray(crop))
    return " ".join(easy(lang).readtext(crop, detail=0, paragraph=True))

def cuts(a, H=1800):  # cut tall strips at the cleanest row
    ys = [0]
    while a.shape[0] - ys[-1] > H * 1.2:
        lo, hi = ys[-1] + H - 300, ys[-1] + H
        ys.append(lo + int(a[lo:hi].std(axis=(1, 2)).argmin()))
    return ys + [a.shape[0]]

# ---------- translation: free Google Translate -> local NLLB fallback ----------
def nllb(texts, src, tgt):
    from transformers import AutoTokenizer, AutoModelForSeq2SeqLM
    S = {"ja": "jpn_Jpan", "ko": "kor_Hang", "zh": "zho_Hans", "es": "spa_Latn", "en": "eng_Latn"}
    T = {"en": "eng_Latn", "es": "spa_Latn", "fr": "fra_Latn", "de": "deu_Latn", "pt": "por_Latn", "it": "ita_Latn",
         "id": "ind_Latn", "vi": "vie_Latn", "tr": "tur_Latn", "ru": "rus_Cyrl"}
    n = "facebook/nllb-200-distilled-600M"
    if "nllb" not in _m: _m["nllb"] = (AutoTokenizer.from_pretrained(n), AutoModelForSeq2SeqLM.from_pretrained(n).to(DEV))
    tk, m = _m["nllb"]; tk.src_lang = S[src]; out = []
    for i in range(0, len(texts), 16):
        x = tk(texts[i:i + 16], return_tensors="pt", padding=True, truncation=True, max_length=128).to(DEV)
        y = m.generate(**x, forced_bos_token_id=tk.convert_tokens_to_ids(T[tgt]), max_new_tokens=128)
        out += tk.batch_decode(y, skip_special_tokens=True)
    return out

LN = {"ja": "Japanese", "ko": "Korean", "zh": "Chinese", "es": "Spanish", "en": "English", "fr": "French", "de": "German",
      "pt": "Portuguese", "it": "Italian", "id": "Indonesian", "vi": "Vietnamese", "tr": "Turkish", "ru": "Russian"}

def opus(texts, src):  # small fast Helsinki-NLP models, X -> English only
    from transformers import MarianMTModel, MarianTokenizer
    n = f"Helsinki-NLP/opus-mt-{src}-en"
    if n not in _m: _m[n] = (MarianTokenizer.from_pretrained(n), MarianMTModel.from_pretrained(n).to(DEV))
    tk, m = _m[n]; out = []
    for i in range(0, len(texts), 16):
        x = tk(texts[i:i + 16], return_tensors="pt", padding=True, truncation=True, max_length=128).to(DEV)
        out += tk.batch_decode(m.generate(**x, max_new_tokens=128, num_beams=4), skip_special_tokens=True)
    return out

QW = {"qwen": ("Qwen/Qwen2.5-3B-Instruct", False), "qwen3-8b": ("Qwen/Qwen3-8B", True),
      "qwen3-14b": ("Qwen/Qwen3-14B", True), "qwen3-32b": ("Qwen/Qwen3-32B", True)}

def qload(cls, n, big):  # big models load in 4-bit on NVIDIA GPUs (bitsandbytes) to fit in VRAM
    if DEV == "cuda" and big:
        try:
            import bitsandbytes  # noqa
            from transformers import BitsAndBytesConfig
            return cls.from_pretrained(n, device_map="auto", quantization_config=BitsAndBytesConfig(
                load_in_4bit=True, bnb_4bit_compute_dtype=torch.float16, bnb_4bit_quant_type="nf4"))
        except Exception as e: print("4-bit load failed, trying fp16:", e)
    return cls.from_pretrained(n, dtype=torch.float16 if DEV == "cuda" else torch.float32).to(DEV)

def qwen(texts, src, tgt, n, big):  # text LLM: one bubble at a time, last 3 lines as context
    from transformers import AutoTokenizer, AutoModelForCausalLM
    if n not in _m: _m[n] = (AutoTokenizer.from_pretrained(n), qload(AutoModelForCausalLM, n, big))
    tk, m = _m[n]; hist = _m.setdefault("hist", []); out = []
    for t in texts:
        msg = [{"role": "system", "content": f"You translate comic dialogue from {LN[src]} to {LN[tgt]}. Reply with only the short, natural translation of the last line."}]
        for a, b in hist[-3:]: msg += [{"role": "user", "content": a}, {"role": "assistant", "content": b}]
        msg.append({"role": "user", "content": t})
        x = tk.apply_chat_template(msg, add_generation_prompt=True, return_tensors="pt", return_dict=True, enable_thinking=False).to(m.device)
        y = m.generate(**x, max_new_tokens=100, do_sample=False)
        s = re.sub(r"<think>.*?</think>", "", tk.decode(y[0][x["input_ids"].shape[1]:], skip_special_tokens=True), flags=re.S).strip() or t
        out.append(s); hist.append((t, s))
    return out

def qwen_vl(texts, crops, src, tgt):  # vision model: sees the bubble image + OCR guess, fixes OCR errors, translates
    from transformers import AutoProcessor, Qwen2_5_VLForConditionalGeneration
    n = "Qwen/Qwen2.5-VL-7B-Instruct"
    if "vl" not in _m: _m["vl"] = (AutoProcessor.from_pretrained(n), qload(Qwen2_5_VLForConditionalGeneration, n, True))
    pr, m = _m["vl"]; out = []
    for t, c in zip(texts, crops):
        im = Image.fromarray(c); im.thumbnail((512, 512))
        msg = [{"role": "user", "content": [{"type": "image"}, {"type": "text", "text":
               f"Comic speech bubble ({LN[src]}). OCR guess: {t}\nTranslate the dialogue into {LN[tgt]}. Fix OCR mistakes using the image. Reply with only the translation."}]}]
        x = pr(text=[pr.apply_chat_template(msg, add_generation_prompt=True)], images=[im], return_tensors="pt").to(m.device)
        y = m.generate(**x, max_new_tokens=100, do_sample=False)
        out.append(pr.batch_decode(y[:, x["input_ids"].shape[1]:], skip_special_tokens=True)[0].strip() or t)
    return out

ORDER = ["google", "bing", "mymemory", "papago", "mirai", "youdao", "reverso", "itranslate", "deepl", "yandex", "sogou", "alibaba", "caiyun", "lingvanex", "modernMt", "qqTranSmart"]
MMN = {k: v.lower() for k, v in LN.items()}; MMN["zh"] = "chinese simplified"

def one(name, t, src, tgt):  # one free online translator, one text
    if name == "google":
        from deep_translator import GoogleTranslator
        return GoogleTranslator(source="auto", target=tgt).translate(t)
    if name == "mymemory":
        from deep_translator import MyMemoryTranslator
        return MyMemoryTranslator(source=MMN[src], target=MMN[tgt]).translate(t)
    import translators as ts  # bing, papago, mirai, youdao, reverso, itranslate...
    return ts.translate_text(t, translator=name, from_language="auto", to_language=tgt)

def _translate(texts, src, tgt, engine="online", crops=None):
    if engine.startswith("api:"):  # AI / API providers from the API panel; on any failure fall back to the free web translators
        pid = engine[4:]; ctx = _m.setdefault("apictx", [])
        try: out = PV.translate(pid, texts, src, tgt, ctx[-6:]); _m["apictx"] = (ctx + out)[-12:]; return out
        except Exception as e:
            _m["note"] = f"{PV.BY[pid]['name']} failed ({str(e)[:180]}). Used the free web translators for these lines."; print(_m["note"]); engine = "online"
    if engine == "opus" and tgt != "en": engine = "nllb"  # Opus-MT models here only go X -> English
    try:
        if engine == "opus": return opus(texts, src)
        if engine == "nllb": return nllb(texts, src, tgt)
        if engine in QW: return qwen(texts, src, tgt, *QW[engine])
        if engine == "qwen-vl": return qwen_vl(texts, crops, src, tgt)
    except Exception as e:
        print(f"{engine} failed ({e}); falling back to Google/NLLB")
    # online engines: the chosen one first, then the others as automatic failover
    chain = [engine] if engine in ORDER else []
    base = ["google", "bing"] + (["papago"] if src == "ko" else ["mirai"] if src == "ja" else []) + ["mymemory"]
    chain = list(dict.fromkeys(chain + base + ORDER))
    dead, fails, used, out = _m.setdefault("dead", {}), {}, {}, []
    for t in texts:
        res = None
        for name in chain:
            if dead.get(name, 0) > time.time(): continue
            try: res = (one(name, t, src, tgt) or "").strip() or None
            except Exception: res = None
            if res: fails[name] = 0; used[name] = used.get(name, 0) + 1; break
            fails[name] = fails.get(name, 0) + 1
            if fails[name] >= 3: dead[name] = time.time() + 300; print(f"{name} keeps failing, skipping it for 5 minutes")
        out.append(res); time.sleep(.15)
    print("Translated via:", used)
    miss = [i for i, o in enumerate(out) if o is None]
    if miss:
        print(len(miss), "texts failed on every online translator, using local NLLB")
        for i, t in zip(miss, nllb([texts[i] for i in miss], src, tgt)): out[i] = t
    return out

_META = re.compile(r"(as an ai\b|language model|i'?m sorry,? but i|i (cannot|can ?not) (assist|fulfil+|comply|translate|help with)|content policy|here(?:'s| is) (?:the|your) translation|^translation\s*:)", re.I)
def tidy(o):
    o = re.sub(r"^```\w*|```$", "", (o or "").strip(), flags=re.M).strip()
    for _ in range(2):
        if len(o) > 1 and o[0] == o[-1] and o[0] in "\"'`": o = o[1:-1].strip()
        o = re.sub(r"^(translation|english)\s*:\s*", "", o, flags=re.I).strip()
    return re.sub(r"\s+", " ", o)

def sane(src, out):
    """False for empty output, an AI refusal / explanation, or text absurdly longer than the source line."""
    return bool(out) and not _META.search(out) and len(out) <= max(80, 8 * len(src))

def translate(texts, src, tgt, engine="online", crops=None):
    """_translate() plus a safety net: lines that look like an AI refusal or explanation are retried on the free web
    translators and, if still bad, left empty (red box) instead of being typeset into the page."""
    out = [tidy(o) for o in _translate(texts, src, tgt, engine, crops)]
    bad = [i for i in range(len(texts)) if not sane(texts[i], out[i])]
    if bad and engine != "online":
        for i, o in zip(bad, _translate([texts[i] for i in bad], src, tgt, "online")): out[i] = tidy(o)
        again = [i for i in bad if not sane(texts[i], out[i])]
    else: again = bad
    for i in again: out[i] = ""
    if bad:
        _m["note"] = (_m.get("note", "") + " " if _m.get("note") else "") + f"{len(bad)} line(s) looked like an AI refusal or explanation: " + (f"{len(again)} left empty (red box), translate those with another engine." if again else "retried with the web translators.")
    return out

# ---------- separate stages: detect -> ocr -> translate -> (edit in UI) -> inpaint -> export ----------
# Page dict: w, h, blocks[{box,src,text,font,size}], st (0 new,1 analyzed,2 read,3 translated), inp (inpainted), rev, run ("", "queued", "active")
def new_pages(w):
    fs = sorted((w / "pages").glob("*.jpg"))
    return [dict(w=Image.open(f).size[0], h=Image.open(f).size[1], blocks=[], st=0, inp=False, rev=0, run="") for f in fs]

def pimg(w, i): return Image.open(w / "pages" / f"{i:05d}.jpg").convert("RGB")
def crop_of(a, box, pad=0):
    H, W = a.shape[:2]; x0, y0, x1, y1 = [int(v) for v in box]
    return a[max(y0 - pad, 0):min(y1 + pad, H), max(x0 - pad, 0):min(x1 + pad, W)]

def loop(j, idx, log, fn):  # runs fn(i) per page, publishes progress, honours the Stop button
    j["total"], j["done"] = len(idx), 0
    for i in idx: j["pages"][i]["run"] = "queued"
    for n, i in enumerate(idx):
        if j.get("stop"): log("Stopped."); break
        j["cur"] = i; j["pages"][i]["run"] = "active"
        try: fn(i, n)
        finally: j["pages"][i]["run"] = ""
        j["done"] = n + 1
    for i in idx: j["pages"][i]["run"] = ""

def stage_detect(j, idx, o, log):
    def f(i, n):
        pg = j["pages"][i]; a = np.array(pimg(j["w"], i)); H, W = a.shape[:2]; bl = []; pg["blocks"] = bl  # list grows live
        log(f"Analyze: page {i + 1} ({n + 1}/{len(idx)})…")
        ys = cuts(a)
        for oy, ey in zip(ys, ys[1:]):
            for b in detect(a[oy:ey]):
                x0, y0, x1, y1 = [int(v) for v in b]; x0, y0 = max(x0, 0), max(y0, 0)
                if x1 - x0 > 8 and y1 - y0 > 8: bl.append(dict(box=[x0, y0 + oy, min(x1, W), y1 + oy], src="", text="", font="", size=0))
        bl.sort(key=lambda b: (b["box"][1] // 40, -b["box"][0] if o["src"] == "ja" else b["box"][0]))
        pg["st"] = 1; pg["inp"] = False; log(f"Analyze: page {i + 1}: {len(bl)} text boxes")
    loop(j, idx, log, f)

def widen(b, W):  # tall vertical text: widen the box so the translation fits
    x0, y0, x1, y1 = b["box"]
    if (y1 - y0) > (x1 - x0) * 1.5:
        cx = (x0 + x1) / 2; nw = max(x1 - x0, (y1 - y0) * .5); b["box"] = [max(int(cx - nw / 2), 0), y0, min(int(cx + nw / 2), W), y1]

def stage_ocr(j, idx, o, log):
    def f(i, n):
        pg = j["pages"][i]; a = np.array(pimg(j["w"], i)); keep = []
        log(f"OCR: page {i + 1} ({n + 1}/{len(idx)}), {len(pg['blocks'])} boxes…")
        for b in list(pg["blocks"]):
            if o["force"] or not b["src"]:
                c = crop_of(a, b["box"])
                t = read(c, o["src"]).strip() if c.size else ""
                if not t: continue  # nothing readable: drop the box
                b["src"] = t; widen(b, pg["w"])
            keep.append(b)
        gone = len(pg["blocks"]) - len(keep); pg["blocks"][:] = keep; pg["st"] = max(pg["st"], 2)
        if gone: log(f"OCR: page {i + 1}: removed {gone} boxes with no readable text")
    loop(j, idx, log, f)

def stage_translate(j, idx, o, log):
    _m["hist"] = []; _m["apictx"] = []
    def f(i, n):
        pg = j["pages"][i]; a = None
        todo = [b for b in pg["blocks"] if b["src"] and (o["force"] or not b["text"])]
        log(f"Translate ({o['engine']}): page {i + 1} ({n + 1}/{len(idx)}), {len(todo)} texts…")
        if todo:
            if o["engine"] == "qwen-vl": a = np.array(pimg(j["w"], i))
            crops = [crop_of(a, b["box"]) for b in todo] if a is not None else None
            for b, t in zip(todo, translate([b["src"] for b in todo], o["src"], o["tgt"], o["engine"], crops)): b["text"] = t
            if _m.get("note"): log(_m.pop("note"))
        pg["st"] = max(pg["st"], 3)
    loop(j, idx, log, f)

def sigof(blocks): return str([[int(v) for v in b["box"]] for b in blocks])

def sweep_boxes(res, lang):
    """Optional extra pass: EasyOCR's CRAFT detector on the already cleaned page finds lettering the first detector missed
    (thin vertical lines, small sound effects). Returns [x0, y0, x1, y1] boxes."""
    rd = easy(lang if lang in ("ja", "ko", "zh", "es", "en") else "en"); H, W = res.shape[:2]; out = []
    ys = cuts(res)
    for oy, ey in zip(ys, ys[1:]):
        h, f = rd.detect(res[oy:ey], text_threshold=.6, low_text=.35, link_threshold=.4, add_margin=.05)
        for x0, x1, y0, y1 in (h[0] if h else []): out.append([x0, y0 + oy, x1, y1 + oy])
        for poly in (f[0] if f else []):
            xs, yy = [p[0] for p in poly], [p[1] for p in poly]; out.append([min(xs), min(yy) + oy, max(xs), max(yy) + oy])
    keep = []
    for x0, y0, x1, y1 in out:
        x0, y0, x1, y1 = max(int(x0), 0), max(int(y0), 0), min(int(x1), W), min(int(y1), H)
        if x1 - x0 >= 10 and y1 - y0 >= 10 and not ((x1 - x0) > .6 * W and (y1 - y0) > .3 * H): keep.append([x0, y0, x1, y1])
    return keep

def clean_page(j, i, sweep=False, lang="ja"):
    """Erase the original lettering and cache the clean page. Per block: Otsu glyph mask -> flat bubbles get a
    feathered colour fill, everything else goes to LaMa on merged padded patches (only masked pixels are replaced).
    sweep=True adds a second look for leftover text on the cleaned page."""
    w = j["w"]; pg = j["pages"][i]; a = np.array(pimg(w, i)); res = a.copy(); m = np.zeros(a.shape[:2], np.uint8)
    def erase(src, box, strict=False):
        x0, y0, x1, y1, mk, bg, frac, ink = text_mask(src, box, strict=strict)
        if mk.size == 0 or not mk.any(): return ink
        if frac > .5 and fast_fill(res, x0, y0, x1, y1, mk): return ink
        m[y0:y1, x0:x1] = np.maximum(m[y0:y1, x0:x1], mk); return ink
    for b in pg["blocks"]: b["ink"] = erase(a, b["box"]) or ""
    if sweep:
        try:
            if m.any(): nn_clean(res, m); m[:] = 0
            extra = sweep_boxes(res, lang)
            if extra: snap = res.copy()
            for bx in extra: erase(snap, bx, strict=True)
            print(f"Extra pass: page {i + 1}: {len(extra)} leftover text areas")
        except Exception as e: print("Extra pass skipped:", e)
    if m.any(): nn_clean(res, m)
    out = Image.fromarray(res); (w / "clean").mkdir(exist_ok=True); out.save(w / "clean" / f"{i:05d}.png", compress_level=1); pg["sig"] = sigof(pg["blocks"])
    return out

def finish_page(j, i, res, S):
    w = j["w"]; pg = j["pages"][i]; clean = np.array(res); res = res.copy()
    if pg["blocks"]: typeset(res, pg["blocks"], S, clean)
    (w / "out").mkdir(exist_ok=True); res.save(w / "out" / f"{i:05d}.jpg", quality=92); pg["inp"] = True; pg["rev"] += 1

def stage_inpaint(j, idx, o, log):
    S = {**STYLE, **(o.get("style") or {})}
    def f(i, n):
        log(f"Inpaint + typeset: page {i + 1} ({n + 1}/{len(idx)})…"); finish_page(j, i, clean_page(j, i, o.get("sweep"), o["src"]), S)
    loop(j, idx, log, f)

def stage_typeset(j, idx, o, log):  # fast: re-draw text on the cached clean page (change font/colour/size, no re-inpainting)
    S = {**STYLE, **(o.get("style") or {})}
    def f(i, n):
        pg = j["pages"][i]; cp = j["w"] / "clean" / f"{i:05d}.png"
        if cp.exists() and pg.get("sig") == sigof(pg["blocks"]): log(f"Re-typeset: page {i + 1} ({n + 1}/{len(idx)})…"); res = Image.open(cp).convert("RGB")
        else: log(f"Page {i + 1}: boxes changed or not cleaned yet, inpainting first…"); res = clean_page(j, i, o.get("sweep"), o["src"])
        finish_page(j, i, res, S)
    loop(j, idx, log, f)

def stage_export(j, o, log):
    w = j["w"]; pack = w / "pack"; shutil.rmtree(pack, ignore_errors=True); pack.mkdir(); orig = 0
    for i in range(len(j["pages"])):
        s = w / "out" / f"{i:05d}.jpg"
        if not s.exists(): s = w / "pages" / f"{i:05d}.jpg"; orig += 1
        shutil.copy(s, pack / f"{i:05d}.jpg")
    log(f"Packing {o['format']}…" + (f" ({orig} pages not inpainted yet, original included)" if orig else ""))
    p, note = export(pack, j["name"] + "_translated", o["format"], w); j["out"] = str(p); log("Exported: " + p.name + (" " + note if note else ""))

def one_box_ocr(w, page, box, lang): 
    c = crop_of(np.array(pimg(w, page)), box); return read(c, lang).strip() if c.size else ""
def one_box_translate(w, page, box, text, src, tgt, engine):
    if engine.startswith("api:"): return PV.translate(engine[4:], [text], src, tgt, _m.get("apictx", [])[-6:])[0]  # errors go straight to the UI
    crops = [crop_of(np.array(pimg(w, page)), box)] if engine == "qwen-vl" else None
    return translate([text], src, tgt, engine, crops)[0]

# ---------- inpainting (swap this function to use another model) ----------
LAMA = MODELS / "lama" / "big-lama.pt"; _lama = [None, False]
def inpaint(img, mask):
    if not _lama[1]:
        _lama[1] = True
        try:
            LAMA.parent.mkdir(exist_ok=True)
            if not LAMA.exists(): urllib.request.urlretrieve("https://github.com/enesmsahin/simple-lama-inpainting/releases/download/v0.1.0/big-lama.pt", LAMA)
            _lama[0] = torch.jit.load(str(LAMA), map_location=DEV).eval()
        except Exception as e: print("LaMa unavailable, using OpenCV inpaint:", e)
    if _lama[0] is None: return Image.fromarray(cv2.inpaint(np.array(img), np.array(mask), 5, cv2.INPAINT_TELEA))
    a = np.array(img).astype("float32") / 255; m = (np.array(mask) > 0).astype("float32")
    h, w = m.shape; pad = (0, (-w) % 8, 0, (-h) % 8)
    x = F.pad(torch.from_numpy(a).permute(2, 0, 1)[None], pad, mode="reflect")
    y = F.pad(torch.from_numpy(m)[None, None], pad, mode="reflect")
    with torch.inference_mode(): o = _lama[0](x.to(DEV), (y.to(DEV) > 0).float())
    return Image.fromarray(np.clip(o[0].permute(1, 2, 0).cpu().numpy()[:h, :w] * 255, 0, 255).astype("uint8"))


def merge_boxes(boxes, shape, pad=32):
    """Pad every (x,y,w,h) mask box and merge the overlapping ones (comic-translate merge_overlapping_padded_boxes)."""
    H, W = shape[:2]; out = []
    for x, y, w, h in boxes:
        x1, y1, x2, y2 = max(0, x - pad), max(0, y - pad), min(W, x + w + pad), min(H, y + h + pad)
        for i, (a, b, c, d) in enumerate(out):
            if not (x2 < a or x1 > c or y2 < b or y1 > d): out[i] = (min(x1, a), min(y1, b), max(x2, c), max(y2, d)); break
        else: out.append((x1, y1, x2, y2))
    ch = True
    while ch:
        ch = False
        for i in range(len(out)):
            for j in range(len(out) - 1, i, -1):
                a, b, c, d = out[i]; e, f, g, h = out[j]
                if not (c < e or a > g or d < f or b > h): out[i] = (min(a, e), min(b, f), max(c, g), max(d, h)); out.pop(j); ch = True
    return out

def flat_color(c, core):
    """Background colour of a bubble crop if it is one flat tone (else None). Mirrors comic-translate's fast-fill checks."""
    halo = cv2.dilate((core > 0).astype(np.uint8), np.ones((3, 3), np.uint8)) > 0
    px = c[~halo].astype(np.float32)
    if px.shape[0] < 64 or (core > 0).mean() > .9: return None
    q = (px // 16).astype(np.int32); keys = (q[:, 0] << 8) | (q[:, 1] << 4) | q[:, 2]
    uk, inv, cnt = np.unique(keys, return_inverse=True, return_counts=True); mc = max(48, int(len(px) * .015)); cand = []
    for k in np.argsort(cnt)[::-1][:16]:
        if cnt[k] < mc: continue
        cl = px[inv == k]; med = np.median(cl, 0); sp = (np.percentile(cl, 90, 0) - np.percentile(cl, 10, 0)).max()
        cand.append((int(cnt[k]), med, float(med.mean()), float(sp)))
    if not cand: return None
    big = cand[0]; br = [x for x in cand if x[2] >= 150 and x[0] >= max(mc, int(big[0] * .12))]
    sel = max(br, key=lambda x: x[0]) if br else big
    v = px[(px @ np.array([.299, .587, .114], np.float32)) >= 50] if sel[2] >= 80 else px  # ignore dark bubble outline
    if v.shape[0] == 0: v = px
    if (np.abs(v - sel[1]) <= 15).all(1).mean() < .55 or sel[3] > 48: return None
    if (np.abs(v - sel[1]) <= 12).all(1).mean() < .94: return None  # artwork / gradient behind the text: use the NN
    return sel[1]

def fast_fill(res, x0, y0, x1, y1, mk):
    c = res[y0:y1, x0:x1]; col = flat_color(c, mk)
    if col is None: return False
    soft = np.clip(cv2.GaussianBlur(mk, (0, 0), 1.0).astype(np.float32) / 255, 0, 1)[..., None]
    c[:] = np.clip(np.round(c.astype(np.float32) * (1 - soft) + col * soft), 0, 255).astype(np.uint8); return True

def nn_paste(res, m, x0, y0, x1, y1):  # inpaint one crop, paste ONLY the masked pixels back
    mk = m[y0:y1, x0:x1]
    if not mk.any(): return
    out = np.array(inpaint(Image.fromarray(res[y0:y1, x0:x1].copy()), Image.fromarray(mk))); sel = mk > 0
    res[y0:y1, x0:x1][sel] = out[sel]

def nn_clean(res, m):
    """Patch or full-page, whichever is cheaper (comic-translate hybrid): masked boxes padded by 32 px and merged;
    if the patches would cost more than the page, run the page in tall strips instead."""
    cs, _ = cv2.findContours((m > 0).astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    bx = merge_boxes([cv2.boundingRect(c) for c in cs], res.shape)
    if not bx: return
    cost = sum(max(128, -(-(x2 - x1) // 8) * 8) * max(128, -(-(y2 - y1) // 8) * 8) for x1, y1, x2, y2 in bx) + (len(bx) - 1) * 50000
    if cost < res.shape[0] * res.shape[1]:
        for x1, y1, x2, y2 in bx: nn_paste(res, m, x1, y1, x2, y2)
    else:
        ys = cuts(res)
        for y0, y1 in zip(ys, ys[1:]): nn_paste(res, m, 0, y0, res.shape[1], y1)

# ---------- fonts: fonts/ folder + system fonts, one global choice, per-box override ----------
SYS_DIRS = [Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts", Path("/Library/Fonts"), Path("/System/Library/Fonts"), Path("/usr/share/fonts")]
def _scan(d): return [p for p in d.rglob("*") if p.suffix.lower() in (".ttf", ".otf")] if d.exists() else []
def list_fonts(): return sorted(p.name for p in FONTS.iterdir() if p.suffix.lower() in (".ttf", ".otf"))
def list_fonts_all():
    sysf = sorted({p.name for d in SYS_DIRS for p in _scan(d)}, key=str.lower)
    return dict(folder=list_fonts(), system=["sys:" + n for n in sysf])
def fpath(name=""):
    if name and (FONTS / name).exists(): return str(FONTS / name)
    if name.startswith("sys:"):
        for d in SYS_DIRS:
            for p in _scan(d):
                if p.name == name[4:]: return str(p)
    fs = list_fonts()
    if fs: return str(FONTS / fs[0])
    for n in ("comicbd.ttf", "comic.ttf", "arialbd.ttf"):  # no custom font: use Comic Sans / Arial Bold if present
        for d in SYS_DIRS:
            if (d / n).exists(): return str(d / n)
    return "arialbd.ttf"
_fc = {}
def font(p, s):
    if (p, s) not in _fc:
        try: _fc[(p, s)] = ImageFont.truetype(p, s)
        except Exception: _fc[(p, s)] = ImageFont.load_default()
    return _fc[(p, s)]

STYLE = dict(font="", color="auto", custom="#000000", case="upper", max=48, min=10, spacing=1.1, outline=-1, hyphen=True, fit=True)
_gl = {}
_SUB = {"♡": "♥", "❤": "♥", "♪": "♪", "♫": "♪", "～": "~", "〜": "~", "…": "...", "‥": "..", "―": "-", "—": "-", "ー": "-",
        "“": '"', "”": '"', "‘": "'", "’": "'", "　": " ", "！": "!", "？": "?", "、": ",", "。": "."}
def _glyph_px(f, ch):
    im = Image.new("L", (64, 64), 0); ImageDraw.Draw(im).text((8, 8), ch, font=f, fill=255); return im.tobytes()

def has_glyph(fp, ch):
    k = (fp, ch)
    if k not in _gl:
        try: f = font(fp, 40); _gl[k] = ch.isspace() or _glyph_px(f, ch) != _gl.setdefault((fp, "notdef"), _glyph_px(f, "\uffff"))  # a missing glyph draws the same .notdef box as U+FFFF
        except Exception: _gl[k] = True
    return _gl[k]

def fix_glyphs(fp, t):
    """Characters the font cannot draw (they would show as empty boxes) become a close substitute, else are dropped."""
    return "".join(ch if has_glyph(fp, ch) else "".join(c for c in _SUB.get(ch, "") if has_glyph(fp, c)) for ch in t)

def hexrgb(h):
    h = (h or "#000000").lstrip("#"); return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4)) if len(h) == 6 else (0, 0, 0)

# ---------- text mask: erase only the lettering, so bubbles / boxes stay intact ----------
def dominant(c):
    q = (c // 16).reshape(-1, 3); k = q[:, 0].astype(int) * 256 + q[:, 1] * 16 + q[:, 2]
    top = np.bincount(k).argmax(); sel = k == top
    return c.reshape(-1, 3)[sel].mean(0)

def content_mask(crop, min_area=10, margin=1):
    """comic-translate style: Otsu threshold, dark AND light glyph candidates as connected components;
    drops components touching the crop border (bubble outline / background) and huge ones (> 50% of the crop)."""
    g = cv2.cvtColor(crop, cv2.COLOR_RGB2GRAY); t, _ = cv2.threshold(g, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    H, W = g.shape; out = np.zeros((H, W), np.uint8)
    for b in ((g < t), (g > t)):
        n, lab, st, _ = cv2.connectedComponentsWithStats(b.astype(np.uint8), connectivity=8)
        if n <= 1: continue
        x, y, w, h, ar = st[1:, 0], st[1:, 1], st[1:, 2], st[1:, 3], st[1:, 4]
        keep = ((ar > min_area) | ((ar >= 4) & (w <= 6) & (h <= 6))) & (x >= margin) & (y >= margin) & (x + w <= W - margin) & (y + h <= H - margin)
        if H * W > 150: keep &= ar < int(.5 * H * W)
        for k in np.flatnonzero(keep):  # long thin lines are bubble outline touching the box, not letters
            if (w[k] > .8 * W and h[k] < .15 * w[k]) or (h[k] > .8 * H and w[k] < .15 * h[k]): keep[k] = False
        if keep.any(): out[np.isin(lab, np.flatnonzero(keep) + 1)] = 255
    return out

def text_mask(a, box, pad=10, strict=False):
    """Returns (x0,y0,x1,y1, mask, bg, frac, ink) for the box padded by `pad`. frac = share of the box that is plain background."""
    H, W = a.shape[:2]; bx0, by0, bx1, by1 = [int(v) for v in box]; bx0, by0, bx1, by1 = max(bx0, 0), max(by0, 0), min(bx1, W), min(by1, H)
    x0, y0, x1, y1 = max(bx0 - pad, 0), max(by0 - pad, 0), min(bx1 + pad, W), min(by1 + pad, H)
    c = a[by0:by1, bx0:bx1]
    if c.size == 0: return x0, y0, x1, y1, np.zeros((0, 0), np.uint8), np.array([255, 255, 255.]), 0, None
    bg = dominant(c); d = np.sqrt(((c.astype(np.float32) - bg) ** 2).sum(2)); frac = float((d < 40).mean())
    core = d > 110; col = np.median(c[core], axis=0) if core.sum() > 15 else None
    ink = None if col is None else "#%02x%02x%02x" % tuple(int(v) for v in col)
    pc = a[y0:y1, x0:x1]; cm = content_mask(pc)
    cm = cv2.morphologyEx(cm, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
    cm = cv2.dilate(cm, np.ones((5, 5), np.uint8), iterations=3)  # glyph edges / antialiasing
    env = np.zeros_like(cm); env[max(by0 - y0 - 8, 0):by1 - y0 + 8, max(bx0 - x0 - 8, 0):bx1 - x0 + 8] = 255  # never reach past the detector box (+8 px)
    cm = cv2.bitwise_and(cm, env)
    if (cm[by0 - y0:by1 - y0, bx0 - x0:bx1 - x0] > 0).mean() < .03:  # nothing text-like found: erase the whole box (strict: leave it alone)
        cm = np.zeros_like(cm) if strict else env.copy()
    return x0, y0, x1, y1, cm, bg, frac, ink

def bubble_rect(a, box):
    """Find the bubble/box interior around a (cleaned) text box. Returns (x0,y0,x1,y1) or None."""
    H, W = a.shape[:2]; x0, y0, x1, y1 = [int(v) for v in box]; x0, y0, x1, y1 = max(x0, 0), max(y0, 0), min(x1, W), min(y1, H)
    if x1 - x0 < 8 or y1 - y0 < 8: return None
    ex = int(max(x1 - x0, y1 - y0) * .8) + 40; wx0, wy0, wx1, wy1 = max(x0 - ex, 0), max(y0 - ex, 0), min(x1 + ex, W), min(y1 + ex, H)
    bg = dominant(a[y0:y1, x0:x1]); sub = a[wy0:wy1, wx0:wx1].astype(np.float32)
    near = (np.sqrt(((sub - bg) ** 2).sum(2)) < 45).astype(np.uint8)
    near = cv2.erode(near, np.ones((3, 3), np.uint8), iterations=2)  # cut thin leaks (bubble tails)
    n, lab = cv2.connectedComponents(near, connectivity=4)
    inner = lab[y0 - wy0:y1 - wy0, x0 - wx0:x1 - wx0]; cy, cx = (y0 + y1) // 2 - wy0, (x0 + x1) // 2 - wx0
    L = lab[cy, cx]
    if L == 0:
        v = inner[inner > 0]
        if v.size == 0: return None
        L = np.bincount(v).argmax()
    ys, xs = np.where(lab == L)
    if ys.size > .6 * near.size: return None  # open background, not a bubble
    rx0, ry0, rx1, ry1 = xs.min() + wx0, ys.min() + wy0, xs.max() + wx0 + 1, ys.max() + wy0 + 1
    ratio = ys.size / max((rx1 - rx0) * (ry1 - ry0), 1); f = float(np.clip(.72 + (ratio - .78) / .22 * .22, .72, .94))  # ellipse -> .72, rectangle -> .94
    cxr, cyr = (rx0 + rx1) / 2, (ry0 + ry1) / 2; bw, bh = x1 - x0, y1 - y0
    w2 = min((rx1 - rx0) * f, bw * 2.2); h2 = min((ry1 - ry0) * f, bh * 2.2)
    w2, h2 = max(w2, bw), max(h2, bh)
    return int(max(cxr - w2 / 2, 0)), int(max(cyr - h2 / 2, 0)), int(min(cxr + w2 / 2, W)), int(min(cyr + h2 / 2, H))

# ---------- text fitting + typesetting ----------
def wrap(f, t, w, brk):
    lines, cur = [], ""
    def put(wd):
        nonlocal cur
        tst = (cur + " " + wd).strip()
        if f.getlength(tst) <= w or not cur: cur = tst
        else: lines.append(cur); cur = wd
    for wd in t.split():
        if f.getlength(wd) <= w or not brk: put(wd); continue
        piece = ""
        for ch in wd:  # hyphenate a word that is wider than the whole line
            if f.getlength(piece + ch + "-") > w and len(piece) >= 2: put(piece + "-"); piece = ""
            piece += ch
        put(piece)
    if cur: lines.append(cur)
    return lines

def fit(t, fp, w, h, S, fixed=0):
    """comic-translate pyside_word_wrap: binary-search the largest size whose greedy pixel-width wrap fits the box;
    nothing fits -> min size with the wrap width that best matches the box (their min-cost search)."""
    sp = float(S["spacing"]); lo0, hi0 = int(S["min"]), int(S["max"])
    if fixed: return fixed, wrap(font(fp, fixed), t, w, S["hyphen"])
    def ok(s, brk):
        f = font(fp, s); ls = wrap(f, t, w, brk); return len(ls) * s * sp <= h and max(f.getlength(l) for l in ls) <= w + 1, ls
    best = {}
    for brk in ([False, True] if S["hyphen"] else [False]):
        lo, hi = lo0, hi0
        while lo <= hi:
            mid = (lo + hi) // 2; good, ls = ok(mid, brk)
            if good: best[brk] = (mid, ls); lo = mid + 1
            else: hi = mid - 1
    if not best:
        s = lo0; f = font(fp, s); cand = []
        for ww in np.linspace(.35 * w, w, 14):
            ls = wrap(f, t, ww, True); cand.append(((max(f.getlength(l) for l in ls) - w) ** 2 + (len(ls) * s * sp - h) ** 2, ls))
        return s, min(cand, key=lambda c: c[0])[1]
    if True in best and (False not in best or best[True][0] >= best[False][0] * 1.2): return best[True]
    return best.get(False) or best[True]

def typeset(img, blocks, S, clean=None):
    d = ImageDraw.Draw(img); a = np.array(img) if clean is None else clean; drawn = []
    for b in blocks:
        t = (b.get("text") or "").strip()
        if not t: continue
        bx = [int(v) for v in b["box"]]  # a box that sits (mostly) inside one already drawn would print its text on top of it
        if any(min(bx[2], q[2]) - max(bx[0], q[0]) > 0 and min(bx[3], q[3]) - max(bx[1], q[1]) > 0 and
               (min(bx[2], q[2]) - max(bx[0], q[0])) * (min(bx[3], q[3]) - max(bx[1], q[1])) >= .8 * (bx[2] - bx[0]) * (bx[3] - bx[1]) for q in drawn): continue
        drawn.append(bx)
        t = t.upper() if S["case"] == "upper" else t.lower() if S["case"] == "lower" else t
        t = fix_glyphs(fpath(b.get("font") or S["font"]), t).strip()
        if not t: continue
        x0, y0, x1, y1 = [int(v) for v in b["box"]]; r = bubble_rect(a, b["box"]) if S["fit"] else None
        if r: x0, y0, x1, y1 = r
        w, h = max(x1 - x0 - 8, 40), max(y1 - y0 - 8, 30); x0 += 4; y0 += 4
        fp = fpath(b.get("font") or S["font"]); s, ls = fit(t, fp, w, h, S, int(b.get("size") or 0)); f = font(fp, s)
        dark = a[y0:y0 + h, x0:x0 + w].mean() < 110 if a[y0:y0 + h, x0:x0 + w].size else False
        c = b.get("color") or ("#000000" if S["color"] == "auto" else S["custom"] if S["color"] == "custom" else b.get("ink") or "#000000")
        col = (255, 255, 255) if (S["color"] == "auto" and not b.get("color") and dark) else hexrgb(c)
        ow = int(S["outline"]); ow = (0 if r else 3) if ow < 0 else ow
        oc = (0, 0, 0) if sum(col) > 380 else (255, 255, 255)
        lh = s * float(S["spacing"]); top = y0 + (h - len(ls) * lh) / 2
        for k, l in enumerate(ls):
            d.text((x0 + (w - f.getlength(l)) / 2, top + k * lh), l, font=f, fill=col, stroke_width=ow, stroke_fill=oc)

# ---------- render + export: cbz / zip / pdf / cbr(rar) / 7z ----------
def export(imgdir, name, fmt, outdir):
    p = outdir / f"{name}.{fmt}"; note = ""; imgs = sorted(imgdir.glob("*.jpg"))
    if fmt in ("cbr", "rar") and not tool("rar", "Rar"): fmt = "cbz"; p = outdir / f"{name}.cbz"; note = "WinRAR not found, saved as CBZ."
    if p.exists(): p.unlink()
    if fmt == "pdf":
        ims = [Image.open(i).convert("RGB") for i in imgs]; ims[0].save(p, save_all=True, append_images=ims[1:])
    elif fmt in ("cbz", "zip"):
        with zipfile.ZipFile(p, "w") as z:
            for i in imgs: z.write(i, i.name)
    elif fmt in ("cbr", "rar"): subprocess.run([tool("rar", "Rar"), "a", "-ep", str(p), "*.jpg"], cwd=imgdir, capture_output=True)
    else: subprocess.run([tool("7z", "7za"), "a", str(p), "*.jpg"], cwd=imgdir, capture_output=True)
    return p, note
