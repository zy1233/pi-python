"""Prints, as JSON, the license data of every installed distribution of this Python.

Run by scripts/notices.mjs inside a clean virtual environment, so the list is the closure of the
packages that the agent binary is built from. Standard library only. (probe)
"""

from __future__ import annotations

import json
import re
import sys
from importlib import metadata

SKIP = {"pip", "setuptools", "wheel"}
LICENSE_FILE = re.compile(r"(^|/)(licen[cs]e|copying|notice|authors)[^/]*$", re.IGNORECASE)


def license_name(dist: metadata.Distribution) -> str:
    meta = dist.metadata
    expression = meta.get("License-Expression")
    if expression:
        return expression
    text = (meta.get("License") or "").strip()
    if text and "\n" not in text and len(text) < 100:
        return text
    classifiers = [
        c.split("::")[-1].strip()
        for c in meta.get_all("Classifier") or []
        if c.startswith("License ::")
    ]
    if classifiers:
        return "; ".join(classifiers)
    return "UNKNOWN"


def license_files(dist: metadata.Distribution) -> list[dict[str, str]]:
    found = []
    for file in dist.files or []:
        name = str(file).replace("\\", "/")
        if ".dist-info/" not in name or not LICENSE_FILE.search(name):
            continue
        try:
            text = file.locate().read_bytes().decode("utf-8", errors="replace")
        except OSError:
            continue
        if text.strip():
            found.append({"name": name.split(".dist-info/", 1)[1], "text": text})
    meta_text = (dist.metadata.get("License") or "").strip()
    if not found and "\n" in meta_text:
        found.append({"name": "METADATA (License)", "text": meta_text})
    return found


def main() -> None:
    out = []
    for dist in metadata.distributions():
        name = dist.metadata["Name"]
        if name is None or name.lower() in SKIP:
            continue
        home = dist.metadata.get("Home-page") or ""
        if not home:
            for url in dist.metadata.get_all("Project-URL") or []:
                home = url.split(",", 1)[-1].strip()
                break
        files = license_files(dist)
        license_label = license_name(dist)
        if license_label == "UNKNOWN" and files:
            first = next(
                (line.strip() for line in files[0]["text"].splitlines() if line.strip()), ""
            )
            license_label = f"see the license text below ({first[:60]})"
        out.append(
            {
                "name": name,
                "version": dist.version,
                "license": license_label,
                "home": home,
                "files": files,
            }
        )
    out.sort(key=lambda d: d["name"].lower())
    json.dump(out, sys.stdout)


if __name__ == "__main__":
    main()
