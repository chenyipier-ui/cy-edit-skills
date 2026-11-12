#!/usr/bin/env python3
"""Portable release smoke checks. Requires Python 3.12, PyAV and FFmpeg.

Use --output outside the checkout. Tests only use generated media and isolated
experience stores; they skip ASR and do not claim human listening acceptance.
Optionally set CY_TEST_FCPXML_DTD to a locally available Apple FCPXML 1.10 DTD.
"""
import argparse
import ast
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
SKILLS = ("cy-edit-shortvideo", "cy-edit-longvideo")


def run(command, **kwargs):
    subprocess.run([str(x) for x in command], check=True, **kwargs)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    out = args.output.resolve()
    if out == ROOT or ROOT in out.parents:
        raise ValueError("Keep generated media and logs outside the repository")
    out.mkdir(parents=True, exist_ok=True)
    if any(out.iterdir()):
        raise ValueError("Use an empty output directory so earlier results cannot pass this run")
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("FFmpeg must be installed for synthetic media checks")
    env = dict(os.environ, CY_TEST_OUTPUT=str(out), PYTHONDONTWRITEBYTECODE="1")
    counts = {"python": 0, "json": 0, "shell": 0, "local_links": 0}
    for path in sorted(ROOT.rglob("*")):
        if not path.is_file() or any(p.startswith(".") for p in path.relative_to(ROOT).parts):
            continue
        if path.suffix == ".py":
            ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            counts["python"] += 1
        elif path.suffix == ".json":
            json.loads(path.read_text(encoding="utf-8"))
            counts["json"] += 1
        elif path.suffix in (".sh", ".command"):
            run(["bash", "-n", path])
            counts["shell"] += 1
        elif path.suffix == ".md":
            for target in re.findall(r"\]\(([^\s)]+)\)", path.read_text(encoding="utf-8")):
                if target.startswith(("http:", "https:", "mailto:", "#")):
                    continue
                dest = (path.parent / target.split("#")[0]).resolve()
                if not dest.exists():
                    raise AssertionError(f"Broken local link in {path.relative_to(ROOT)}: {target}")
                counts["local_links"] += 1
    for name in SKILLS:
        skill = ROOT / "skills" / name
        text = (skill / "SKILL.md").read_text(encoding="utf-8")
        assert text.startswith("---\n") and f"name: {name}\n" in text
        assert "description: " in text.split("---", 2)[1]
        assert (skill / "LICENSE").read_bytes() == (ROOT / "LICENSE").read_bytes()
        assert (skill / "VERSION").read_text().strip() == (ROOT / "VERSION").read_text().strip()
        with (out / f"{name}-help.txt").open("w", encoding="utf-8") as log:
            run([sys.executable, skill / "scripts/editor.py", "--help"], env=env, stdout=log)
    run([ffmpeg, "-nostdin", "-y", "-v", "error", "-f", "lavfi", "-i",
         "color=c=0x99aab3:s=320x180:r=25:d=12", "-f", "lavfi", "-i",
         "sine=frequency=440:sample_rate=48000:duration=12", "-c:v", "libx264",
         "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac",
         "-af", "volume=0.02", "-shortest", out / "fixture.mp4"])
    for script in ("smoke_components.py", "smoke_short.py", "smoke_review.py"):
        with (out / f"{script}.log").open("w", encoding="utf-8") as log:
            run([sys.executable, ROOT / "tests" / script], env=env, stdout=log, stderr=subprocess.STDOUT)
        print(f"PASS {script}", flush=True)
    xmls = []
    for delivery, project_count in ((out / "components/long/deliveries/v1", 2),
                                    (out / "short/delivery", 1),
                                    (out / "short/delivery-r4", 1)):
        files = list(delivery.iterdir())
        assert len(files) == 1 and files[0].suffix == ".fcpxml", files
        tree = ET.parse(files[0])
        assert tree.getroot().get("version") == "1.10"
        assert len(tree.findall(".//project")) == project_count
        if os.environ.get("CY_TEST_FCPXML_DTD"):
            run(["xmllint", "--noout", "--dtdvalid", os.environ["CY_TEST_FCPXML_DTD"], files[0]])
        xmls.append({"path": str(files[0].relative_to(out)), "projects": project_count,
                     "sha256": hashlib.sha256(files[0].read_bytes()).hexdigest()})
    result = {"status": "passed", "version": (ROOT / "VERSION").read_text().strip(),
              "checks": counts, "fcpxml": xmls,
              "dtd": "passed" if os.environ.get("CY_TEST_FCPXML_DTD") else "not_run",
              "limits": ["synthetic media", "ASR skipped", "no human content or listening acceptance",
                         "no native editor UI exercised by these scripts"]}
    (out / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
