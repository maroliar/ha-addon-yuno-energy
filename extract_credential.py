"""Extracts Yuno Energy's shared app-level API credential from the official
Android app, so you don't have to take this repo's word for it - or ship it
with the repo. See EXTRACTING_CREDENTIAL.md for background on what this
value is and why the add-on needs it.

Usage:
    python extract_credential.py YunoEnergy.xapk
    python extract_credential.py YunoEnergy.apk
"""

import io
import re
import sys
import zipfile
from pathlib import Path

ASCII_RUN = re.compile(rb"[\x20-\x7e]{4,}")
SHAPE = re.compile(r"^[A-Za-z][A-Za-z0-9]*(-[A-Za-z0-9]+)+:[^\s]{8,40}$")


def get_classes_dex(path: Path) -> bytes:
    with zipfile.ZipFile(path) as z:
        names = z.namelist()
        if "classes.dex" in names:
            return z.read("classes.dex")
        for name in names:
            if name.endswith(".apk"):
                with zipfile.ZipFile(io.BytesIO(z.read(name))) as inner:
                    if "classes.dex" in inner.namelist():
                        return inner.read("classes.dex")
    raise SystemExit(
        "Could not find classes.dex inside that file - is this a valid "
        "Yuno Energy .apk or .xapk?"
    )


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit(f"Usage: python {sys.argv[0]} <YunoEnergy.apk-or-.xapk>")

    dex = get_classes_dex(Path(sys.argv[1]))
    strings = (m.group().decode("ascii") for m in ASCII_RUN.finditer(dex))
    candidates = sorted({s for s in strings if SHAPE.match(s)})

    if not candidates:
        print("No candidates found. Yuno may have changed the app since this "
              "guide was written - see the project's README for how this was "
              "originally found (full APK decompilation).")
        return

    print(f"Found {len(candidates)} candidate(s):\n")
    for c in candidates:
        print(f"  {c}")
    print("\nUse this exact value (including the colon) as your "
          "`yuno_app_credential` add-on option.")


if __name__ == "__main__":
    main()
