"""
Scan the Audio/ directory and rebuild config/audio_assets_map.json based on
the names stored in AMTA sections of every .bars file.

Run from repo root:
    python scripts/generate_audio_assets_map.py
"""

import json
import re
import struct
import sys
from collections import Counter
from io import BytesIO
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from mk8dx_audio_classes import Amta


ROOT_AUDIO = REPO_ROOT / "Audio"
OUTPUT_PATH = REPO_ROOT / "config" / "audio_assets_map.json"
_existing_map = {}


def read_bars_metas(path: Path):
    """Yield (meta_names) for a .bars file without loading assets."""
    with path.open("rb") as f:
        if f.read(4) != b"BARS":
            raise ValueError("Not a BARS file")

        size_bytes = f.read(4)
        bom_bytes = f.read(2)
        if bom_bytes not in (b"\xFE\xFF", b"\xFF\xFE"):
            raise ValueError("Invalid BOM")
        bom = ">" if bom_bytes == b"\xFE\xFF" else "<"

        version_bytes = f.read(2)
        meta_count_bytes = f.read(4)
        size, version_minor, version_major, meta_count = struct.unpack(
            bom + "I2BI", size_bytes + version_bytes + meta_count_bytes
        )

        crcs = struct.unpack(bom + "I" * meta_count, f.read(4 * meta_count)) if meta_count else []

        meta_offsets = []
        asset_offsets = []
        for _ in range(meta_count):
            mo, ao = struct.unpack(bom + "2I", f.read(8))
            meta_offsets.append(mo)
            asset_offsets.append(ao)

        # Skip padding/unknown up to first meta
        if meta_count:
            to_skip = meta_offsets[0] - f.tell()
            if to_skip > 0:
                f.read(to_skip)

        names = []
        all_offsets = meta_offsets + asset_offsets + [size]
        for mo in meta_offsets:
            next_off = min(o for o in all_offsets if o > mo)
            meta_size = next_off - mo
            f.seek(mo)
            meta_bytes = f.read(meta_size)
            amta = Amta(BytesIO(meta_bytes))
            if getattr(amta, "name", ""):
                names.append(amta.name)

        return names


def derive_prefix(names):
    cand = Counter()
    for n in names:
        m = re.search(r"(?:VO|SE)_([A-Z0-9]+)", n)
        if m:
            cand[m.group(1)] += 1
    return cand.most_common(1)[0][0] if cand else ""


def main():
    global _existing_map
    if OUTPUT_PATH.exists():
        try:
            _existing_map = json.loads(OUTPUT_PATH.read_text(encoding="utf-8"))
        except Exception:
            _existing_map = {}

    output = {}
    bars_files = sorted(ROOT_AUDIO.rglob("*.bars"))
    print(f"Found {len(bars_files)} .bars files under {ROOT_AUDIO}")

    for idx, path in enumerate(bars_files, 1):
        rel = path.relative_to(ROOT_AUDIO)
        section = str(rel.parent).replace("\\", "/")
        entry = {"prefix": "", "amta": [], "bfwav": []}

        try:
            names = read_bars_metas(path)
        except Exception as e:
            print(f"[WARN] Skip {path}: {e}")
            continue

        entry["amta"] = names
        entry["bfwav"] = names  # 1:1 mapping assumption
        # Reuse existing prefix if available; otherwise derive.
        prior_prefix = _existing_map.get(section, {}).get(rel.name, {}).get("prefix", "")
        entry["prefix"] = prior_prefix or derive_prefix(names)

        output.setdefault(section, {})[rel.name] = entry

        if idx % 20 == 0:
            print(f"Processed {idx}/{len(bars_files)}")

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(output, indent=2), encoding="utf-8")
    print(f"Written {OUTPUT_PATH} (sections: {len(output)})")


if __name__ == "__main__":
    main()
