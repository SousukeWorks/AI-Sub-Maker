"""Preserve installed dependency license texts when distributing the desktop build."""
from importlib import metadata
from pathlib import Path
import sys


def collect(target: Path):
    sections = ["AI-Sub-Maker third-party notices\n"]
    lockfile = Path(__file__).with_name('requirements-lock.txt')
    included = {line.split('==')[0].strip().lower().replace('_', '-') for line in lockfile.read_text('utf-8').splitlines()
                if '==' in line and not line.startswith('#')}
    for distribution in sorted(metadata.distributions(), key=lambda d: d.metadata.get("Name", "").lower()):
        name = distribution.metadata.get("Name", "unknown")
        if name.lower().replace('_', '-') not in included:
            continue
        if name.lower() in {"pip", "pyinstaller", "pyinstaller-hooks-contrib", "pefile", "pywin32-ctypes", "altgraph"}:
            continue
        sections.append(f"\n{'=' * 72}\n{name} {distribution.version}\n")
        for field in ("License", "License-Expression", "Home-page", "Project-URL"):
            for value in distribution.metadata.get_all(field, []):
                sections.append(f"{field}: {value}\n")
        for item in distribution.files or []:
            if any(part.lower().startswith(("license", "copying", "notice", "copyright")) for part in Path(str(item)).parts):
                path = Path(distribution.locate_file(item))
                if path.is_file() and path.stat().st_size < 1_000_000:
                    sections.append(f"\n--- {item} ---\n{path.read_text(encoding='utf-8', errors='replace')}\n")
    for name in ("LICENSE.txt", "LICENSE"):
        path = Path(sys.base_prefix) / name
        if path.is_file():
            sections.append(f"\nPython runtime license\n{path.read_text(encoding='utf-8', errors='replace')}\n")
            break
    dictionary_notice = Path(__file__).with_name('JANOME-NOTICE.txt')
    if dictionary_notice.is_file():
        sections.append('\nJanome / MeCab-IPADIC dictionary notice\n' + dictionary_notice.read_text(encoding='utf-8'))
    target.write_text("".join(sections), encoding="utf-8")


if __name__ == "__main__":
    collect(Path(sys.argv[1]))
