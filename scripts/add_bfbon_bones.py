"""
Add missing bones (.bfbon) from MK8D_Bones into a driver .szs (BFRES) skeleton.

Usage:
    python scripts/add_bfbon_bones.py \
        --szs MK8D \
        --bones-dir MK8D_Bones \
        --vanilla-szs characters/BbDaisy/Driver/BbDaisy.szs

Requirements:
    - pythonnet (pip install pythonnet)
    - Syroot DLLs in scripts/lib/ (Syroot.BinaryData.dll, Syroot.Maths.dll,
      Syroot.NintenTools.NSW.Bfres.dll) – these are bundled from Switch Toolbox.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set

import oead

DLL_NAMES = (
    "Syroot.BinaryData.dll",
    "Syroot.Maths.dll",
    "Syroot.NintenTools.NSW.Bfres.dll",
)


def load_bfres_libs(lib_dir: Path):
    """Load the Syroot BFRES assemblies via pythonnet."""
    try:
        import clr  # type: ignore
    except ImportError as exc:  # pragma: no cover - dependency check
        raise SystemExit(
            "pythonnet is required (pip install pythonnet). "
            f"Details: {exc}"
        ) from exc

    sys.path.insert(0, str(lib_dir))
    missing = [name for name in DLL_NAMES if not (lib_dir / name).exists()]
    if missing:
        raise SystemExit(
            "Missing required DLLs in scripts/lib/: " + ", ".join(missing)
        )
    for name in DLL_NAMES:
        clr.AddReference(str(lib_dir / name))

    from Syroot.NintenTools.NSW.Bfres import Bone, Model, ResFile, Skeleton  # type: ignore
    from System import Array, Byte  # type: ignore
    from System.IO import MemoryStream  # type: ignore

    return Bone, Model, ResFile, Skeleton, Array, Byte, MemoryStream


def decompress_if_needed(path: Path) -> bytes:
    data = path.read_bytes()
    return oead.yaz0.decompress(data) if data[:4] == b"Yaz0" else data


def load_resfile(
    ResFile, MemoryStream, Array, Byte, path: Path
):  # type: ignore
    raw = decompress_if_needed(path)
    stream = MemoryStream(Array[Byte](raw))
    return ResFile(stream)


def gather_bone_paths(bones_dir: Path) -> Dict[str, Path]:
    bone_paths: Dict[str, Path] = {}
    for bfbon in bones_dir.glob("*.bfbon"):
        bone_paths[bfbon.stem] = bfbon
    if not bone_paths:
        raise SystemExit(f"No .bfbon files found in {bones_dir}")
    return bone_paths


def build_order(
    base_skeleton,
    available: Set[str],
) -> List[str]:
    if base_skeleton is None:
        return sorted(available)
    ordered: List[str] = []
    for bone in base_skeleton.Bones:
        name = bone.Name
        if name in available:
            ordered.append(name)
    # Add any leftover names not present in the base skeleton
    leftovers = [name for name in available if name not in ordered]
    ordered.extend(sorted(leftovers))
    return ordered


def add_missing_bones_to_skeleton(
    skeleton,
    bone_paths: Dict[str, Path],
    order: Iterable[str],
    Bone,
) -> List[str]:
    added: List[str] = []
    existing: Set[str] = {str(b.Name) for b in skeleton.Bones}
    for name in order:
        if name in existing:
            continue
        path = bone_paths.get(name)
        if path is None:
            continue
        bone = Bone()
        bone.Import(str(path))
        skeleton.Bones.Add(bone)
        skeleton.BoneDict.Add(bone.Name)
        added.append(name)
        existing.add(name)
    return added


def save_resfile(res, MemoryStream, Array, Byte, out_path: Path):
    stream = MemoryStream()
    res.Save(stream, True)
    stream.Position = 0
    fres_bytes = bytes(bytearray(stream.ToArray()))
    compressed = oead.yaz0.compress(fres_bytes)
    out_path.write_bytes(compressed)


def main():
    repo_root = Path(__file__).resolve().parent.parent
    parser = argparse.ArgumentParser(
        description="Inject MK8D_Bones .bfbon files into a BFRES skeleton."
    )
    parser.add_argument(
        "--szs",
        type=Path,
        default=repo_root / "NormalMawile/Driver/BbDaisy.szs",
        help="Target .szs BFRES to patch.",
    )
    parser.add_argument(
        "--bones-dir",
        type=Path,
        default=repo_root / "MK8D_Bones",
        help="Folder containing .bfbon files to import.",
    )
    parser.add_argument(
        "--vanilla-szs",
        type=Path,
        default=repo_root / "characters/BbDaisy/Driver/BbDaisy.szs",
        help="Optional vanilla .szs used to order bones.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Optional output path. Defaults to in-place overwrite.",
    )
    args = parser.parse_args()

    lib_dir = Path(__file__).resolve().parent / "lib"
    Bone, Model, ResFile, Skeleton, Array, Byte, MemoryStream = load_bfres_libs(lib_dir)

    bone_paths = gather_bone_paths(args.bones_dir)
    base_skel = None
    if args.vanilla_szs.exists():
        base_res = load_resfile(ResFile, MemoryStream, Array, Byte, args.vanilla_szs)
        base_skel = base_res.Models[0].Skeleton

    res = load_resfile(ResFile, MemoryStream, Array, Byte, args.szs)

    initial_names = {str(b.Name) for b in res.Models[0].Skeleton.Bones}
    desired_names = set(bone_paths.keys())
    order = build_order(base_skel, desired_names)

    for model in res.Models:
        added = add_missing_bones_to_skeleton(
            model.Skeleton, bone_paths, order, Bone
        )

    out_path = args.output or args.szs
    if out_path == args.szs:
        backup = out_path.with_suffix(out_path.suffix + ".bak")
        if not backup.exists():
            out_path.replace(backup)
    save_resfile(res, MemoryStream, Array, Byte, out_path)

    final_names = {str(b.Name) for b in res.Models[0].Skeleton.Bones}
    added_unique = sorted((final_names - initial_names) & desired_names)
    if added_unique:
        print(f"Added {len(added_unique)} bones: {', '.join(added_unique)}")
    else:
        print("No bones were added; skeleton already contained all .bfbon files.")


if __name__ == "__main__":
    main()
