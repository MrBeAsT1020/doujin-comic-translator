"""AI + API translation providers. Keys live in config.json next to app.py (local only, never sent anywhere but the provider)."""
import json, re, time, threading
from pathlib import Path
import requests

import paths
CFG = paths.APP / "config.json"
LN = {"ja": "Japanese", "ko": "Korean", "zh": "Chinese", "es": "Spanish", "en": "English", "fr": "French", "de": "German",
      "pt": "Portuguese", "it": "Italian", "id": "Indonesian", "vi": "Vietnamese", "tr": "Turkish", "ru": "Russian"}

# kind: openai (any OpenAI-compatible /chat/completions) | gemini | anthropic | deepl | libre
# free: what the free tier looks like (these change often: check the provider's page)
CATALOG = [
    dict(id="gemini", name="Google Gemini", kind="gemini", base="https://generativelanguage.googleapis.com", model="gemini-2.5-flash", rpm=10,
         free="Free tier in AI Studio, no card. Limits differ per model (about 10 req/min on Flash).", url="https://aistudio.google.com/apikey"),
    dict(id="groq", name="Groq", kind="openai", base="https://api.groq.com/openai/v1", model="llama-3.3-70b-versatile", rpm=30,
         free="Free, no card. About 30 req/min, thousands of req/day. Also hosts Kimi K2.", url="https://console.groq.com/keys"),
    dict(id="openrouter", name="OpenRouter", kind="openai", base="https://openrouter.ai/api/v1", model="openrouter/free", rpm=15,
         free="Models ending in :free cost nothing (about 20 req/min, 50 req/day; 1000/day after a $10 top-up). The free list rotates, so the default openrouter/free picks a working free model for you. Press Models to pick a specific :free one. Free hosts may log prompts.", url="https://openrouter.ai/keys"),
    dict(id="mistral", name="Mistral", kind="openai", base="https://api.mistral.ai/v1", model="mistral-small-latest", rpm=50,
         free="Free mode on La Plateforme (phone check). About 1 req/s.", url="https://console.mistral.ai/api-keys"),
    dict(id="cerebras", name="Cerebras", kind="openai", base="https://api.cerebras.ai/v1", model="gpt-oss-120b", rpm=30,
         free="Free tier with daily token limits (can change).", url="https://cloud.cerebras.ai"),
    dict(id="nvidia", name="NVIDIA NIM", kind="openai", base="https://integrate.api.nvidia.com/v1", model="meta/llama-3.3-70b-instruct", rpm=30,
         free="Free developer access, rate limited.", url="https://build.nvidia.com"),
    dict(id="deepl", name="DeepL", kind="deepl", base="", model="", rpm=60,
         free="DeepL API Free: 500,000 characters/month. Keys ending in :fx use the free endpoint.", url="https://www.deepl.com/pro-api"),
    dict(id="libre", name="LibreTranslate", kind="libre", base="http://localhost:5000", model="", rpm=120, needs_key=False,
         free="Free if you self-host it (default http://localhost:5000). The public site needs a paid key.", url="https://github.com/LibreTranslate/LibreTranslate"),
    dict(id="kimi", name="Kimi (Moonshot)", kind="openai", base="https://api.moonshot.ai/v1", model="kimi-k2-0905-preview", rpm=20,
         free="Paid API (small sign-up credit sometimes). For free Kimi use the Groq or OpenRouter cards with a Kimi model.", url="https://platform.moonshot.ai"),
    dict(id="deepseek", name="DeepSeek", kind="openai", base="https://api.deepseek.com/v1", model="deepseek-chat", rpm=60,
         free="Paid, very cheap. No permanent free tier.", url="https://platform.deepseek.com/api_keys"),
    dict(id="openai", name="OpenAI (ChatGPT)", kind="openai", base="https://api.openai.com/v1", model="gpt-4o-mini", rpm=60,
         free="Paid API. A ChatGPT subscription does not include API credit.", url="https://platform.openai.com/api-keys"),
    dict(id="claude", name="Anthropic (Claude)", kind="anthropic", base="https://api.anthropic.com", model="claude-haiku-5-5", rpm=50,
         free="Paid API. A Claude.ai subscription does not include API credit.", url="https://console.anthropic.com/settings/keys"),
    dict(id="perplexity", name="Perplexity", kind="openai", base="https://api.perplexity.ai", model="sonar", rpm=30,
         free="Paid API (it is a search model, fine for short lines but not the best translator).", url="https://www.perplexity.ai/settings/api"),
    dict(id="ollama", name="Ollama (local)", kind="openai", base="http://localhost:11434/v1", model="qwen2.5:7b", rpm=600, needs_key=False,
         free="Free, runs on your PC. Install Ollama and run: ollama pull qwen2.5:7b", url="https://ollama.com"),
    dict(id="lmstudio", name="LM Studio (local)", kind="openai", base="http://localhost:1234/v1", model="", rpm=600, needs_key=False,
         free="Free, runs on your PC. Start the local server in LM Studio and load a model.", url="https://lmstudio.ai"),
    dict(id="pollinations", name="Pollinations (no key)", kind="openai", base="https://text.pollinations.ai/openai", model="openai", rpm=4, needs_key=False,
         free="No key, very slow and may change or disappear. Last resort.", url="https://pollinations.ai"),
    dict(id="custom", name="Custom OpenAI-compatible", kind="openai", base="", model="", rpm=60, needs_key=False,
         free="Any server that speaks the OpenAI chat API (set the base URL ending in /v1).", url=""),
]
BY = {c["id"]: c for c in CATALOG}
_lock = threading.Lock(); _last = {}

def load():
    try: return json.loads(CFG.read_text(encoding="utf-8"))
    except Exception: return {}

def save(c): CFG.write_text(json.dumps(c, indent=1), encoding="utf-8")

def cfg(pid):
    c = dict(BY[pid]); c.setdefault("needs_key", True); c.update({k: v for k, v in load().get(pid, {}).items() if v not in (None, "")}); return c

def usable(pid):
    c = cfg(pid)
    if not c["needs_key"] and pid not in load(): return False  # local / keyless servers only count once you pressed Save or Test
    return bool((c.get("key") or not c["needs_key"]) and (c.get("base") or c["kind"] in ("deepl", "anthropic", "gemini")) and (c.get("model") or c["kind"] in ("deepl", "libre")))

def listing():
    s = load(); out = []
    for c in CATALOG:
        v = cfg(c["id"]); k = s.get(c["id"], {}).get("key", "")
        out.append(dict(id=c["id"], name=c["name"], kind=c["kind"], base=v.get("base", ""), model=v.get("model", ""), rpm=v.get("rpm", 30),
                        free=c["free"], url=c["url"], needs_key=v["needs_key"], has_key=bool(k), hint=("…" + k[-4:]) if k else "", ok=usable(c["id"])))
    return out

def update(pid, d):
    if pid not in BY: raise KeyError(pid)
    s = load(); e = s.setdefault(pid, {})
    for k in ("model", "base"):
        if k in d: e[k] = str(d[k]).strip()
    if "rpm" in d:
        try: e["rpm"] = max(1, min(1000, int(d["rpm"])))
        except Exception: pass
    if d.get("clear"): e.pop("key", None)
    elif d.get("key"): e["key"] = str(d["key"]).strip()
    save(s)

def pace(pid, rpm):  # keep under the provider's requests-per-minute
    gap = 60.0 / max(1, rpm)
    with _lock:
        wait = _last.get(pid, 0) + gap - time.time(); _last[pid] = max(time.time(), _last.get(pid, 0) + gap)
    if wait > 0: time.sleep(wait)

def post(pid, c, url, **kw):
    for n in range(4):
        pace(pid, int(c.get("rpm", 30)))
        try: r = requests.post(url, timeout=90, **kw)
        except requests.RequestException as e: raise RuntimeError(f"{c['name']}: cannot reach the server ({type(e).__name__}). Check the base URL / that the local server is running.")
        if r.status_code in (429, 500, 502, 503, 529) and n < 3:
            try: ra = float(r.headers.get("retry-after", ""))
            except Exception: ra = 0
            time.sleep(min(max(ra, 2 ** (n + 1)), 30)); continue
        if r.status_code >= 400: raise RuntimeError(f"{c['name']} HTTP {r.status_code}: {r.text[:200]}")
        return r.json()

def system_prompt(src, tgt, n):
    return (f"You are a professional translator of manga, manhwa and comics. Translate each numbered line from {LN.get(src, src)} to {LN.get(tgt, tgt)}. "
            "Keep the tone, slang, honorifics and character voice natural. Keep lines short enough for speech bubbles. "
            f"Sound effects: translate briefly or keep the onomatopoeia. Do not add notes. Reply with ONLY a JSON array of exactly {n} strings, in the same order.")

def llm_call(c, system, user):
    k = c.get("key", ""); kind = c["kind"]
    if kind == "gemini":
        d = post(c["id"], c, f"{c['base'].rstrip('/')}/v1beta/models/{c['model']}:generateContent", headers={"x-goog-api-key": k},
                 json={"systemInstruction": {"parts": [{"text": system}]}, "contents": [{"role": "user", "parts": [{"text": user}]}],
                       "generationConfig": {"temperature": .2, "responseMimeType": "application/json"}})
        return "".join(p.get("text", "") for p in d["candidates"][0]["content"]["parts"])
    if kind == "anthropic":
        d = post(c["id"], c, f"{c['base'].rstrip('/')}/v1/messages", headers={"x-api-key": k, "anthropic-version": "2023-06-01"},
                 json={"model": c["model"], "max_tokens": 2048, "temperature": .2, "system": system, "messages": [{"role": "user", "content": user}]})
        return "".join(b.get("text", "") for b in d["content"])
    h = {"Authorization": f"Bearer {k}"} if k else {}
    if c["id"] == "openrouter": h.update({"HTTP-Referer": "http://localhost:8000", "X-Title": "Doujin Comic Translator"})
    d = post(c["id"], c, f"{c['base'].rstrip('/')}/chat/completions", headers=h,
             json={"model": c["model"], "temperature": .2, "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]})
    return d["choices"][0]["message"]["content"] or ""

def parse_list(s, n):
    s = re.sub(r"<think>.*?</think>", "", s or "", flags=re.S).strip()
    a, b = s.find("["), s.rfind("]")
    if a < 0 or b < a: return None
    try: v = json.loads(s[a:b + 1])
    except Exception: return None
    return [str(x).strip() for x in v] if isinstance(v, list) and len(v) == n else None

DL = {"en": "EN-US", "pt": "PT-BR", "zh": "ZH"}
def deepl(c, texts, src, tgt):
    k = c.get("key", ""); host = "https://api-free.deepl.com" if k.endswith(":fx") else "https://api.deepl.com"
    d = post("deepl", c, host + "/v2/translate", headers={"Authorization": "DeepL-Auth-Key " + k},
             json={"text": texts, "source_lang": src.upper(), "target_lang": DL.get(tgt, tgt.upper())})
    return [t["text"] for t in d["translations"]]

def libre(c, texts, src, tgt):
    d = post("libre", c, c["base"].rstrip("/") + "/translate", json={"q": texts, "source": src, "target": tgt, "format": "text", "api_key": c.get("key", "")})
    t = d["translatedText"]; return t if isinstance(t, list) else [t]

def translate(pid, texts, src, tgt, ctx=()):
    """Translate a list of lines with one provider. Raises on failure so the caller can fall back."""
    c = cfg(pid)
    if c["needs_key"] and not c.get("key"): raise RuntimeError(f"{c['name']}: add your API key in the API panel first")
    if c["kind"] == "deepl": return deepl(c, texts, src, tgt)
    if c["kind"] == "libre": return libre(c, texts, src, tgt)
    out = []
    for i in range(0, len(texts), 15):
        part = texts[i:i + 15]; sysm = system_prompt(src, tgt, len(part))
        user = (("Previous lines for context only (already translated, do not repeat them):\n" + "\n".join(ctx[-6:]) + "\n\n") if ctx else "") + \
               "Lines to translate:\n" + json.dumps(part, ensure_ascii=False)
        r = parse_list(llm_call(c, sysm, user), len(part))
        if r is None:  # model broke the format: ask again line by line
            r = []
            for t in part:
                x = parse_list(llm_call(c, system_prompt(src, tgt, 1), json.dumps([t], ensure_ascii=False)), 1)
                if x is None: raise RuntimeError(f"{c['name']} returned an unreadable answer")
                r += x
        out += r
    return out

def test(pid):
    t0 = time.time()
    try:
        r = translate(pid, ["こんにちは、元気ですか？"], "ja", "en")
        return dict(ok=True, text=r[0], ms=int((time.time() - t0) * 1000))
    except Exception as e: return dict(ok=False, error=str(e)[:300])

def models(pid):
    c = cfg(pid); k = c.get("key", ""); kind = c["kind"]
    if kind == "gemini":
        d = requests.get(c["base"].rstrip("/") + "/v1beta/models?pageSize=200", headers={"x-goog-api-key": k}, timeout=30); d.raise_for_status()
        return sorted(m["name"].split("/", 1)[-1] for m in d.json().get("models", []) if "generateContent" in m.get("supportedGenerationMethods", []))
    if kind == "anthropic":
        d = requests.get(c["base"].rstrip("/") + "/v1/models", headers={"x-api-key": k, "anthropic-version": "2023-06-01"}, timeout=30); d.raise_for_status()
        return [m["id"] for m in d.json().get("data", [])]
    if kind == "openai":
        d = requests.get(c["base"].rstrip("/") + "/models", headers={"Authorization": f"Bearer {k}"} if k else {}, timeout=30); d.raise_for_status()
        return sorted(m["id"] for m in d.json().get("data", []))
    return []
