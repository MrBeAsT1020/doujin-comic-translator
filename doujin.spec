# PyInstaller spec for the Windows build:   python -m PyInstaller doujin.spec --noconfirm
# Result: dist/DoujinTranslator/DoujinTranslator.exe (a folder build: models are downloaded into  models/  next to the exe on first use)
from PyInstaller.utils.hooks import collect_all, collect_submodules

datas = [("static", "static"), ("doujin_logo.ico", ".")]
binaries, hidden = [], collect_submodules("uvicorn") + collect_submodules("python_multipart") + ["multipart", "email.mime.multipart"]
for pkg in ("easyocr", "manga_ocr", "ultralytics", "translators", "gallery_dl", "pymupdf", "huggingface_hub", "transformers",
            "fugashi", "unidic_lite", "jaconv", "deep_translator", "sentencepiece", "bitsandbytes", "accelerate"):
    try:
        d, b, h = collect_all(pkg); datas += d; binaries += b; hidden += h
    except Exception as e:
        print("spec: skipping", pkg, "->", e)

a = Analysis(["launcher.py"], pathex=["."], binaries=binaries, datas=datas, hiddenimports=hidden, excludes=["tkinter", "matplotlib", "IPython", "pytest"], noarchive=False)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="DoujinTranslator", icon="doujin_logo.ico", console=False, upx=False)
coll = COLLECT(exe, a.binaries, a.datas, name="DoujinTranslator", upx=False)
