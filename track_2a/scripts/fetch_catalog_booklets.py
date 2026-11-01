"""Load the catalog's booklets that are missing locally and check each one against the catalog's SHA-256.

A file counts as present only if it starts with %PDF- and has the catalog's hash. Everything else is downloaded
from the official URL to <name>.part, verified and renamed in one step, so a failed or wrong download never leaves a
file under the booklet's name. Existing files are never deleted. Safe to run again; it prints one status per file.

Usage (from track_2a/):
  python scripts/fetch_catalog_booklets.py [--only 2026-09-27 2026-11-29]
"""
import argparse
import hashlib
import os
from pathlib import Path
import sys
from typing import Dict
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src import config
from src.booklet_catalog import load_catalog

TIMEOUT_SECONDS = 90
USER_AGENT = "FactAttack-catalog-fetch/1.0"


def _digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _is_valid(path: Path, sha256: str) -> bool:
    try:
        raw = path.read_bytes()
    except OSError:
        return False
    return raw.startswith(b"%PDF-") and _digest(raw) == sha256


def fetch_missing(sources: Dict[str, dict], directory: Path, *, opener=urllib.request.urlopen) -> Dict[str, str]:
    """Make every booklet of `sources` (the output of `load_catalog`) available in `directory`.

    Returns {file name: "vorhanden" | "geladen"}. A download that is not a PDF or whose SHA-256 differs from the
    catalog raises ValueError; the target file is then not written and no <name>.part is left behind.
    """
    directory = Path(directory)
    status = {}
    for name, entry in sources.items():
        sha256 = entry.get("sha256")
        if not sha256:
            raise ValueError(f"{name}: Der Katalogeintrag hat keinen SHA-256, die Datei lässt sich nicht prüfen")
        target = directory / name
        if _is_valid(target, sha256):
            status[name] = "vorhanden"
            continue
        directory.mkdir(parents=True, exist_ok=True)
        request = urllib.request.Request(entry["url"], headers={"User-Agent": USER_AGENT})
        with opener(request, timeout=TIMEOUT_SECONDS) as response:
            raw = response.read()
        if not raw.startswith(b"%PDF-"):
            raise ValueError(f"{name}: Von {entry['url']} kam kein PDF")
        if _digest(raw) != sha256:
            raise ValueError(f"{name}: Der SHA-256 der geladenen Datei ({_digest(raw)}) weicht vom Katalog ({sha256}) ab")
        part = target.with_name(name + ".part")
        try:
            part.write_bytes(raw)
            os.replace(part, target)
        finally:
            part.unlink(missing_ok=True)
        status[name] = "geladen"
    return status


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--catalog", type=Path, default=config.DATA_DIR / "booklet_catalog.json")
    parser.add_argument("--booklets", type=Path, default=config.BOOKLETS_DIR)
    parser.add_argument("--only", nargs="+", metavar="DATUM",
                        help="nur die Büchlein dieser Abstimmungsdaten (JJJJ-MM-TT), Standard: alle")
    args = parser.parse_args()
    sources = load_catalog(args.catalog)
    if args.only:
        unknown = sorted(set(args.only) - {entry["date"] for entry in sources.values()})
        if unknown:
            parser.error("Nicht im Katalog: " + ", ".join(unknown))
        sources = {name: entry for name, entry in sources.items() if entry["date"] in args.only}
    try:
        status = fetch_missing(sources, args.booklets)
    except (ValueError, OSError) as error:
        raise SystemExit(f"Fehler: {error}")
    for name, state in status.items():
        print(f"{state:<10} {name}")
    print(f"{len(status)} Büchlein geprüft, {sum(s == 'geladen' for s in status.values())} geladen")


if __name__ == "__main__":
    main()
