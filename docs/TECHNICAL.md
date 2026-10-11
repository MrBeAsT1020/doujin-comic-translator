# Technical notes

Deeper notes on how the pieces work (erasing, fitting, translators, building the .exe). The main README is [one level up](../README.md).

# Comic Translator (Windows, local web app)
1. Install Python 3.11 or 3.12 (tick "Add to PATH") and 7-Zip (needed for rar/7z input; WinRAR is needed only for rar/cbr output).
2. Double-click `run.bat`. First run installs everything (answer y if you have an NVIDIA GPU; if the CUDA wheel fails, take the command from pytorch.org).
3. Browser opens at http://127.0.0.1:8000
4. Drop custom fonts (.ttf/.otf) into the `fonts` folder; they appear in the editor's font list.
Models download into `models/` on first use and are reused.
Flow: Analyze (detect + OCR + translate) -> edit boxes/text/font/size -> Inpaint & export.

## Text style (new)
- The "Text style (all boxes)" panel sets font, colour, case, sizes, line spacing and outline for every box at once. A per-box choice in the edit panel still overrides it; "Reset every box" clears those overrides.
- Fonts: everything in the `fonts` folder plus your Windows system fonts. For a manga look, drop a comic font (.ttf/.otf) into `fonts`, for example Bangers, Komika Axis or Anime Ace, then reload the page.
- 6 Inpaint erases the old lettering and draws the translation. 6b Re-typeset only redraws the text on the already cleaned pages, so changing the font or colour takes seconds.

## Look (new)
- Put your logo in `static/` as `logo.png` (or .webp/.jpg/.svg). It shows in the header and as the tab icon. Until then a placeholder is used. Hard-refresh (Ctrl+F5) after adding it.
- Left rail: Import, Config (languages, engine, scope), Style (text), API (next stage), Export (format + download).
- Top bar: the steps with kanji labels, Run 2-4, Stop, progress.

## Erasing + text fitting (comic-translate style)
- Mask: Otsu threshold on dark AND light glyphs, connected components (border-touching and huge ones dropped), close 3x3, dilate 5x5 x3, clipped to the box +8 px.
- Flat bubbles: filled with the bubble's own colour (feathered 1 px). Everything else: LaMa on padded (32 px) merged patches, or on tall strips when patches would cost more than the page. Only masked pixels are replaced.
- Fit: binary search for the largest font size whose pixel-width wrap fits the box; if nothing fits, min size with the best-matching wrap width.

## AI / API translators (API panel)
Left rail -> API. Save a key, press Test, then pick the engine in Config (it appears under "AI / API"). Keys live in `config.json` next to app.py (keep it private). If an API fails, the free web translators take over and the log says why.
- Free tiers (limits change often): OpenRouter (:free models, default `openrouter/free`), Google Gemini, Groq, Mistral, Cerebras, NVIDIA NIM, DeepL API Free (500k chars/month), LibreTranslate (self-host), Ollama / LM Studio (local), Pollinations (no key, last resort).
- Paid: OpenAI (ChatGPT), Anthropic (Claude), Kimi, DeepSeek, Perplexity. Chat subscriptions do not include API access.
- One request per 15 bubbles, paced by the "Req / min" box so free rate limits are respected. "Models" lists what your key can use; OpenRouter accepts several models separated by commas.

## Adjusting boxes by hand
Edit mode: click a box, drag it to move, drag a white handle to resize. Arrow keys nudge 1 px (Shift = 10 px, Alt + arrows = resize). X / Y / W / H fields in the right panel for exact values. Click empty page area to deselect. Changed boxes are re-cleaned automatically on the next Inpaint / Re-typeset.

## Run it
- Double-click `run.bat` (first run creates a venv and installs everything), or `python launcher.py`. It opens in its own app window and closes with the window. The Quit button (left rail) also stops it. Logs: `logs/app.log`.
- `python app.py` still works (opens your browser, no auto-quit).

## Load from a link
Paste a chapter URL (or a direct link to a .cbz / .zip / .pdf / image) and press Load. Handles lazy-loaded images, srcset, pages listed in scripts, hotlink protection (sends the page as Referer) and falls back to gallery-dl for sites it knows. Sites behind Cloudflare / login cannot be downloaded automatically: save the chapter from your browser (or with gallery-dl) and load the folder / cbz.

## Standalone Windows .exe
Needs to be built on Windows (it cannot be built on Mac/Linux):
1. In the Python env you already use (`conda activate comic`) run `build_exe.bat`.
2. Result: `dist\DoujinTranslator\DoujinTranslator.exe` (folder build, icon = `doujin_logo.ico`). Zip and share the whole folder.
3. Models download into `models\` next to the exe the first time each feature is used. Fonts go in `fonts\`, keys in `config.json`, a logo override in `static\logo.png`, all next to the exe.
4. Without a Windows PC: push this folder to a GitHub repo and run the "Build Windows exe" action (CPU-only build).
The first build usually needs a missing-module fix or two (PyInstaller + torch/easyocr): send me the last lines of the build output.

## Cleanup upgrades
- Extra cleanup pass (Config): scans the cleaned page again for leftover text (vertical lines, small sound effects) and erases only the letter strokes. Slower; can clip artwork, so it is off by default.
- Translation guard: lines that look like an AI refusal / explanation are retried on the web translators, else left empty (red box). Text is cleaned of quotes / "Translation:" prefixes.
- Font glyph fallback: characters the font cannot draw (shown as empty boxes) are swapped for close substitutes or dropped.
- Boxes lying inside another box are not typeset twice.
- Page strip shows thumbnails.
