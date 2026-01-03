"""
Replace BFWAV audio entries in a .bars using BarsLibrary.dll and group mappings.

Example:
    python scripts/replace_bfwav_with_groups.py --src Driver_BbPeach.bars --dst Driver_DK.bars --output Driver_DK_modified.bars

It uses config/bfwav_groups.json: any destination entry whose name appears in a
group will be replaced with the first source entry in the same group (or an
exact name match if present). A .bak backup is made when overwriting --dst.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

BFRES_DLLS = (
    "Syroot.BinaryData.dll",
    "Syroot.Maths.dll",
    "Syroot.NintenTools.NSW.Bfres.dll",
)


def load_bars_lib(lib_dir: Path):
    """Load BarsLibrary.dll and dependencies via pythonnet."""
    try:
        import clr  # type: ignore
    except ImportError as exc:  # pragma: no cover - dependency
        raise SystemExit("pythonnet is required: pip install pythonnet") from exc

    sys.path.insert(0, str(lib_dir))
    missing = [name for name in (*BFRES_DLLS, "BarsLibrary.dll") if not (lib_dir / name).exists()]
    if missing:
        raise SystemExit(f"Missing DLLs in {lib_dir}: {', '.join(missing)}")

    for name in BFRES_DLLS:
        clr.AddReference(str(lib_dir / name))
    clr.AddReference(str(lib_dir / "BarsLibrary.dll"))

    from BarsLib import BARS  # type: ignore

    return BARS


def load_groups(groups_path: Path):
    data = json.loads(groups_path.read_text(encoding="utf-8"))
    groups = data.get("groups", [])
    name_to_group = {}
    for idx, names in enumerate(groups):
        for name in names:
            name_to_group[name] = idx
    return groups, name_to_group


def replace_with_groups(src_path: Path, dst_path: Path, out_path: Path, groups_path: Path):
    lib_dir = Path(__file__).resolve().parent / "lib"
    BARS = load_bars_lib(lib_dir)

    groups, name_to_group = load_groups(groups_path)

    src_bars = BARS(str(src_path))
    dst_bars = BARS(str(dst_path))

    src_by_name = {str(entry.MetaData.Name): entry for entry in src_bars.AudioEntries}
    group_to_src_entries: dict[int, list] = {}
    for name, entry in src_by_name.items():
        gid = name_to_group.get(name)
        if gid is not None:
            group_to_src_entries.setdefault(gid, []).append(entry)

    replaced = 0
    missing = []

    for entry in dst_bars.AudioEntries:
        dest_name = str(entry.MetaData.Name)
        src_entry = src_by_name.get(dest_name)
        if not src_entry:
            gid = name_to_group.get(dest_name)
            candidates = group_to_src_entries.get(gid) if gid is not None else None
            if candidates:
                src_entry = candidates[0]
            else:
                missing.append(dest_name)
                continue

        # Copy metadata/audio; keep destination name for consistency.
        entry.MetaData = src_entry.MetaData
        try:
            entry.MetaData.Name = dest_name
        except Exception:
            pass
        entry.AudioFile = src_entry.AudioFile
        replaced += 1

    if out_path == dst_path:
        backup = dst_path.with_suffix(dst_path.suffix + ".bak")
        if not backup.exists():
            dst_path.replace(backup)
        else:
            backup.write_bytes(dst_path.read_bytes())

    dst_bars.Save(str(out_path))
    print(f"Replaced {replaced} entries in {out_path.name}. Missing in source groups: {len(missing)}")
    if missing:
        print("Missing names:", ", ".join(missing[:10]) + ("..." if len(missing) > 10 else ""))


def main():
    repo_root = Path(__file__).resolve().parents[1]
    default_groups = repo_root / "config" / "bfwav_groups.json"

    parser = argparse.ArgumentParser(description="Replace BFWAV entries using group mappings and BarsLibrary.dll")
    parser.add_argument("--src", required=True, type=Path, help="Source .bars containing desired BFWAVs")
    parser.add_argument("--dst", required=True, type=Path, help="Destination .bars to modify")
    parser.add_argument("--output", type=Path, help="Output path (defaults to overwriting --dst)")
    parser.add_argument("--groups", type=Path, default=default_groups, help="Path to bfwav_groups.json")
    args = parser.parse_args()

    out_path = args.output or args.dst
    replace_with_groups(args.src, args.dst, out_path, args.groups)


if __name__ == "__main__":
    main()
