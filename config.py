"""User settings; remembered API keys are protected by Windows DPAPI."""
from __future__ import annotations

import base64
import ctypes
import json
import os
import sys
from ctypes import wintypes
from dataclasses import asdict, fields
from pathlib import Path

from engine import Settings, data_dir
from subtitles import atomic_write

SECRET_FIELDS = ("deepseek_key", "soniox_key")


def local_soniox_key(*, consume=False) -> str:
    """Optional per-Windows-account bootstrap, excluded from distributable ZIPs."""
    folder = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).parent
    path = folder / "soniox-key.dpapi"
    if not path.is_file():
        return ""
    value = protect(path.read_text(encoding="ascii").strip(), decrypt=True)
    if consume:
        path.unlink()
    return value


def protect(value: str, decrypt: bool = False) -> str:
    if not value:
        return ""
    if os.name != "nt":
        raise RuntimeError("记住 API Key 需要 Windows 数据保护服务")

    class Blob(ctypes.Structure):
        _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_ubyte))]

    raw = base64.b64decode(value, validate=True) if decrypt else value.encode("utf-8")
    buffer = ctypes.create_string_buffer(raw)
    source = Blob(len(raw), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    result = Blob()
    crypt = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    function = crypt.CryptUnprotectData if decrypt else crypt.CryptProtectData
    function.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
    function.restype = wintypes.BOOL
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    if not function(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(result)):
        raise RuntimeError("Windows 无法加密或解密 API Key")
    try:
        output = ctypes.string_at(result.data, result.size)
        return output.decode("utf-8") if decrypt else base64.b64encode(output).decode("ascii")
    finally:
        kernel.LocalFree(result.data)


def load() -> tuple[Settings, bool, str]:
    path = data_dir() / "settings.json"
    if not path.exists():
        return Settings(), False, ""
    try:
        document = json.loads(path.read_text(encoding="utf-8-sig"))
        allowed = {field.name for field in fields(Settings)} - set(SECRET_FIELDS)
        old_values = document['settings']
        values = {key: value for key, value in old_values.items() if key in allowed}
        if not values.get('temp_folder') and isinstance(old_values.get('model_cache_folder'), str):
            legacy_folder = old_values['model_cache_folder'].strip()
            if legacy_folder and Path(legacy_folder).is_absolute():
                values['temp_folder'] = str(Path(legacy_folder) / '_temp')
        defaults = asdict(Settings())
        for name, value in values.items():
            if type(defaults[name]) is float and type(value) is int:
                values[name] = float(value)
        if any(type(value) is not type(defaults[key]) for key, value in values.items()):
            raise ValueError("配置字段类型无效")
        settings = Settings(**values)
        # Only used to validate old paid-result fingerprints; never sent to an API.
        settings._legacy_context_for_cache = old_values.get('prompt', '') if isinstance(old_values.get('prompt', ''), str) else ''
        remember = bool(document.get("remember_keys"))
        if remember:
            for name in SECRET_FIELDS:
                setattr(settings, name, protect(document.get(name, ""), decrypt=True))
        return settings, remember, ""
    except Exception:
        return Settings(), False, "之前的设置无法读取，已使用默认值。若更换了 Windows 账户，请重新填写 API Key。"


def save(settings: Settings, remember: bool):
    values = asdict(settings)
    for name in SECRET_FIELDS:
        values.pop(name)
    document = {"version": 2, "settings": values, "remember_keys": remember}
    if remember:
        for name in SECRET_FIELDS:
            document[name] = protect(getattr(settings, name))
    atomic_write(data_dir() / "settings.json", json.dumps(document, ensure_ascii=False, indent=2))
