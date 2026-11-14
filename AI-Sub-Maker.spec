# Build a standalone Windows x64 folder. No Python or FFmpeg installation required.
from PyInstaller.utils.hooks import collect_all, copy_metadata
from pathlib import Path
import ssl

datas, binaries, hiddenimports = [], [], []
# This Python distribution loads an additional CA policy file whenever an SSL
# context is created. Keep the resource beside frozen ssl.py so HTTPS works on
# first use, while preserving the runtime's original certificate validation.
tls_policy = Path(ssl.__file__).with_name('codex-rejected-cas.pem')
if tls_policy.is_file():
    datas.append((str(tls_policy), '.'))
for package in ('av', 'janome'):
    d, b, h = collect_all(package)
    datas += d
    binaries += b
    hiddenimports += h
for package in ('av', 'numpy', 'requests', 'Janome'):
    datas += copy_metadata(package)

a = Analysis(['app.py'], pathex=[], binaries=binaries, datas=datas,
    hiddenimports=hiddenimports,
    excludes=['faster_whisper', 'ctranslate2', 'onnxruntime', 'tokenizers', 'huggingface_hub', 'nvidia', 'torch', 'tensorflow', 'transformers', 'matplotlib', 'pandas', 'scipy', 'IPython'],
    noarchive=False)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='AI-Sub-Maker',
    debug=False, bootloader_ignore_signals=False, strip=False, upx=False,
    console=False, disable_windowed_traceback=False)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='AI-Sub-Maker')
