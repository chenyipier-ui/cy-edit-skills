#!/usr/bin/env python3
"""Build deterministic standalone skill ZIPs and a SHA-256 manifest."""
import argparse
import hashlib
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parents[1]
SKILLS = ("cy-edit-shortvideo", "cy-edit-longvideo")


def write_zip(path, names):
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name in names:
            folder = ROOT / "skills" / name
            for source in sorted(folder.rglob("*")):
                if not source.is_file() or any(p in (".DS_Store", "__pycache__", ".git") for p in source.parts):
                    continue
                if source.suffix in (".pyc", ".pyo"):
                    continue
                if source.is_symlink():
                    raise ValueError(f"Unexpected symlink: {source}")
                info = zipfile.ZipInfo(f"{name}/{source.relative_to(folder).as_posix()}", (2026, 11, 12, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                info.create_system = 3
                mode = 0o755 if source.suffix in (".sh", ".command") else 0o644
                info.external_attr = (0o100000 | mode) << 16
                data = source.read_bytes()
                if source.suffix in (".cmd", ".ps1"):
                    data = data.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")
                archive.writestr(info, data)
    with zipfile.ZipFile(path) as archive:
        assert archive.testzip() is None
        for name in names:
            assert f"{name}/SKILL.md" in archive.namelist()
            assert archive.read(f"{name}/LICENSE") == (ROOT / "LICENSE").read_bytes()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    out = args.output.resolve()
    if out == ROOT or ROOT in out.parents:
        raise ValueError("Write release assets outside the repository")
    out.mkdir(parents=True, exist_ok=True)
    version = (ROOT / "VERSION").read_text().strip()
    archives = []
    for name in SKILLS:
        assert (ROOT / "skills" / name / "VERSION").read_text().strip() == version
        file = out / f"{name}-v{version}.zip"
        write_zip(file, [name])
        archives.append(file)
    file = out / f"cy-edit-skills-v{version}.zip"
    write_zip(file, SKILLS)
    archives.append(file)
    lines = [f"{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.name}" for p in archives]
    (out / "SHA256SUMS").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
