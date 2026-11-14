from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED
import hashlib
import json

from app import VERSION


def pack(destination, files, base):
    with ZipFile(destination, "w", compression=ZIP_DEFLATED, compresslevel=6) as archive:
        for path in sorted(files):
            archive.write(path, path.relative_to(base).as_posix())
    with ZipFile(destination) as archive:
        if archive.testzip() is not None:
            raise RuntimeError("ZIP integrity check failed")
        return len(archive.infolist())


def file_hash(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def main():
    root = Path(__file__).resolve().parent
    dist = root / "dist"
    release = dist / VERSION
    folder = release / "AI-Sub-Maker"
    if not (folder / "AI-Sub-Maker.exe").is_file():
        raise RuntimeError("Build the executable before packaging")
    binary = dist / f"AI-Sub-Maker-{VERSION}-Windows11-x64.zip"
    # A local DPAPI bootstrap may be added after packaging for the user's own EXE.
    # Never distribute credentials, even encrypted ones, in a public portable ZIP.
    def include(path):
        if not path.is_file() or path.name in {"soniox-key.dpapi", "lyrics-library.json", "member-library.json"}:
            return False
        return True
    entries = pack(binary, [p for p in folder.rglob("*") if include(p)], release)
    source = dist / f"AI-Sub-Maker-{VERSION}-source.zip"
    files = [p for p in root.iterdir() if p.is_file() and (p.suffix in {".py", ".md", ".txt", ".ps1", ".bat", ".spec"} or p.name in {'.gitignore', '.gitattributes'})]
    files += [p for p in (root / '.github').rglob('*') if p.is_file() and p.suffix in {'.yml', '.yaml'}]
    pack(source, files, root.parent)
    artifacts = []
    for path in (binary, source, folder / "AI-Sub-Maker.exe"):
        artifacts.append({"name": path.name, "bytes": path.stat().st_size, "sha256": file_hash(path)})
    (dist / f"release-manifest-{VERSION}.json").write_text(json.dumps({"version": VERSION, "package_type": "complete", "binary_entries": entries, "artifacts": artifacts}, indent=2), encoding="utf-8")
    print(json.dumps(artifacts, indent=2), flush=True)


if __name__ == "__main__":
    main()
