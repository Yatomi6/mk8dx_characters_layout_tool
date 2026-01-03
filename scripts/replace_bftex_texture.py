#!/usr/bin/env python3
from __future__ import annotations

"""
CLI to replace the pixel data of a .bftex texture with an RGBA PNG.

Supported:
- Little-endian BRTI/BRTD layout like the sample in test_textures/.
- Uncompressed RGBA8/BGRA8 formats (format high byte 0x0B or 0x0C).
- Single-mip textures (numMips == 1).

The script swizzles the PNG into the block-linear layout used by Switch BNTX
textures and writes it over the existing data region. A .bak backup is written
when patching in-place (unless you pick --out).
"""

import argparse
import struct
from pathlib import Path
from typing import Iterable, Sequence

import oead
from PIL import Image

# Matches the TextureInfo struct from BNTX (see BNTX Editor by AboodXD).
TEX_INFO_FMT = "<2B4H2x2I3i3I20x3IB3x8q"

# format high-byte -> block dimensions (width, height)
BLOCK_DIMS = {
    # BCn and ASTC entries kept for completeness; uncompressed defaults to (1, 1).
    0x1A: (4, 4),
    0x1B: (4, 4),
    0x1C: (4, 4),
    0x1D: (4, 4),
    0x1E: (4, 4),
    0x1F: (4, 4),
    0x20: (4, 4),
    0x2D: (4, 4),
    0x2E: (5, 4),
    0x2F: (5, 5),
    0x30: (6, 5),
    0x31: (6, 6),
    0x32: (8, 5),
    0x33: (8, 6),
    0x34: (8, 8),
    0x35: (10, 5),
    0x36: (10, 6),
    0x37: (10, 8),
    0x38: (10, 10),
    0x39: (12, 10),
    0x3A: (12, 12),
}

# format high-byte -> bytes per pixel (uncompressed) or per block (compressed)
BPP_MAP = {
    0x01: 0x01,
    0x02: 0x01,
    0x03: 0x02,
    0x04: 0x02,
    0x05: 0x02,
    0x06: 0x02,
    0x07: 0x02,
    0x08: 0x02,
    0x09: 0x02,
    0x0B: 0x04,
    0x0C: 0x04,
    0x0E: 0x04,
    0x1A: 0x08,
    0x1B: 0x10,
    0x1C: 0x10,
    0x1D: 0x08,
    0x1E: 0x10,
    0x1F: 0x10,
    0x20: 0x10,
    0x2D: 0x10,
    0x2E: 0x10,
    0x2F: 0x10,
    0x30: 0x10,
    0x31: 0x10,
    0x32: 0x10,
    0x33: 0x10,
    0x34: 0x10,
    0x35: 0x10,
    0x36: 0x10,
    0x37: 0x10,
    0x38: 0x10,
    0x39: 0x10,
    0x3A: 0x10,
    0x3B: 0x02,
}


def div_round_up(n: int, d: int) -> int:
    return (n + d - 1) // d


def round_up(x: int, align: int) -> int:
    return (x + align - 1) & ~(align - 1)


def pow2_round_up(x: int) -> int:
    x -= 1
    x |= x >> 1
    x |= x >> 2
    x |= x >> 4
    x |= x >> 8
    x |= x >> 16
    return x + 1


def get_addr_block_linear(x: int, y: int, image_width: int, bpp: int, block_height: int) -> int:
    """Block-linear address calculation (Tegra X1)."""
    image_width_in_gobs = div_round_up(image_width * bpp, 64)
    gob_addr = (
        (y // (8 * block_height)) * 512 * block_height * image_width_in_gobs
        + (x * bpp // 64) * 512 * block_height
        + (y % (8 * block_height) // 8) * 512
    )
    x_bytes = x * bpp
    return (
        gob_addr
        + ((x_bytes % 64) // 32) * 256
        + ((y % 8) // 2) * 64
        + ((x_bytes % 32) // 16) * 32
        + (y % 2) * 16
        + (x_bytes % 16)
    )


def _swizzle(
    width: int,
    height: int,
    blk_width: int,
    blk_height: int,
    round_pitch: bool,
    bpp: int,
    tile_mode: int,
    block_height_log2: int,
    data: bytes,
    to_swizzle: bool,
) -> bytes:
    block_height = 1 << block_height_log2
    width_blocks = div_round_up(width, blk_width)
    height_blocks = div_round_up(height, blk_height)

    if tile_mode == 1:
        pitch = width_blocks * bpp
        if round_pitch:
            pitch = round_up(pitch, 32)
        surf_size = pitch * height_blocks
    else:
        pitch = round_up(width_blocks * bpp, 64)
        surf_size = pitch * round_up(height_blocks, block_height * 8)

    result = bytearray(surf_size)
    for y in range(height_blocks):
        for x in range(width_blocks):
            pos = (
                y * pitch + x * bpp
                if tile_mode == 1
                else get_addr_block_linear(x, y, width_blocks, bpp, block_height)
            )
            pos_linear = (y * width_blocks + x) * bpp
            if pos + bpp > surf_size:
                continue
            if to_swizzle:
                result[pos : pos + bpp] = data[pos_linear : pos_linear + bpp]
            else:
                result[pos_linear : pos_linear + bpp] = data[pos : pos + bpp]
    return bytes(result)


def swizzle(
    width: int,
    height: int,
    blk_width: int,
    blk_height: int,
    round_pitch: bool,
    bpp: int,
    tile_mode: int,
    block_height_log2: int,
    data: bytes,
) -> bytes:
    return _swizzle(width, height, blk_width, blk_height, round_pitch, bpp, tile_mode, block_height_log2, data, True)


def parse_texture_info(data: bytes, offset: int) -> dict[str, int | list[int]]:
    keys = [
        "flags",
        "dim",
        "tileMode",
        "swizzle",
        "numMips",
        "numSamples",
        "fmt",
        "accessFlags",
        "width",
        "height",
        "depth",
        "arrayLength",
        "textureLayout",
        "textureLayout2",
        "imageSize",
        "alignment",
        "compSel",
        "imgDim",
        "nameAddr",
        "parentAddr",
        "ptrsAddr",
        "userDataAddr",
        "texPtr",
        "texViewPtr",
        "descSlotDataAddr",
        "userDictAddr",
    ]
    values = struct.unpack_from(TEX_INFO_FMT, data, offset)
    info = dict(zip(keys, values))
    info["blockHeightLog2"] = info["textureLayout"] & 7
    info["compSelList"] = [(info["compSel"] >> (8 * i)) & 0xFF for i in range(4)]
    return info


def apply_component_select(pixels: bytes, selectors: Sequence[int]) -> bytes:
    """Reorder RGBA bytes according to component selectors."""
    if list(selectors) == [2, 3, 4, 5]:
        return pixels
    out = bytearray()
    for i in range(0, len(pixels), 4):
        r, g, b, a = pixels[i : i + 4]
        for sel in selectors:
            if sel == 0:
                out.append(0x00)
            elif sel == 1:
                out.append(0xFF)
            elif sel == 2:
                out.append(r)
            elif sel == 3:
                out.append(g)
            elif sel == 4:
                out.append(b)
            elif sel == 5:
                out.append(a)
            else:
                out.append(0x00)
    return bytes(out)


def load_png_pixels(png_path: Path, expected_size: tuple[int, int]) -> bytes:
    with Image.open(png_path) as img:
        img = img.convert("RGBA")
        if img.size != expected_size:
            raise SystemExit(f"PNG dimensions {img.size} do not match texture size {expected_size}.")
        return img.tobytes()


def find_brti_offset(data: bytes) -> int:
    brti = data.find(b"BRTI")
    if brti == -1:
        raise SystemExit("BRTI block not found in bftex.")
    return brti + 16  # skip the 16-byte block header


def read_first_mip_offset(data: bytes, ptrs_addr: int) -> int:
    return struct.unpack_from("<q", data, ptrs_addr)[0]


def read_string(data: bytes, pos: int) -> str:
    size = struct.unpack_from("<H", data, pos)[0]
    return data[pos + 2 : pos + 2 + size].decode("utf-8")


def patch_texture_bytes_multi(data: bytes, png_map: dict[str, Path]) -> tuple[bytes | None, set[str]]:
    """Patch textures inside a BNTX/BFTEX blob. Returns (patched bytes or None, set of matched names)."""
    matches = []
    search_pos = 0
    while True:
        brti = data.find(b"BRTI", search_pos)
        if brti == -1:
            break
        matches.append(brti + 16)
        search_pos = brti + 4

    patched = bytearray(data)
    touched: set[str] = set()

    for tex_info_offset in matches:
        info = parse_texture_info(data, tex_info_offset)
        name = read_string(data, info["nameAddr"])
        if name not in png_map:
            continue

        if info["numMips"] != 1:
            raise SystemExit(
                f"Only single-mip textures are supported (found numMips={info['numMips']} for {name})."
            )

        fmt_high = (info["fmt"] >> 8) & 0xFF
        if fmt_high not in (0x0B, 0x0C):
            raise SystemExit(f"Unsupported format 0x{info['fmt']:04x}; only RGBA8/BGRA8 are handled.")

        bpp = BPP_MAP.get(fmt_high)
        if bpp is None:
            raise SystemExit(f"No BPP mapping for format 0x{info['fmt']:04x}.")
        blk_w, blk_h = BLOCK_DIMS.get(fmt_high, (1, 1))

        png_pixels = load_png_pixels(png_map[name], (info["width"], info["height"]))
        png_pixels = apply_component_select(png_pixels, info["compSelList"])

        block_height_shift = 0
        lines_per_block_height = (1 << info["blockHeightLog2"]) * 8
        height_blocks = div_round_up(info["height"], blk_h)
        if pow2_round_up(height_blocks) < lines_per_block_height:
            block_height_shift += 1
        level_log2 = max(0, info["blockHeightLog2"] - block_height_shift)

        swizzled = swizzle(
            info["width"],
            info["height"],
            blk_w,
            blk_h,
            round_pitch=True,
            bpp=bpp,
            tile_mode=info["tileMode"],
            block_height_log2=level_log2,
            data=png_pixels,
        )

        if len(swizzled) < info["imageSize"]:
            swizzled = swizzled.ljust(info["imageSize"], b"\0")
        elif len(swizzled) > info["imageSize"]:
            swizzled = swizzled[: info["imageSize"]]

        mip_offset = read_first_mip_offset(data, info["ptrsAddr"])
        end_offset = mip_offset + info["imageSize"]
        if end_offset > len(data):
            raise SystemExit("Computed data range exceeds file size; aborting.")

        patched[mip_offset:end_offset] = swizzled
        touched.add(name)

    return (bytes(patched) if touched else None, touched)


def patch_bftex_file(bftex_path: Path, png_path: Path, target_name: str | None, out_path: Path | None) -> Path:
    data = bftex_path.read_bytes()
    names = {target_name or png_path.stem: png_path}
    patched, touched = patch_texture_bytes_multi(data, names)
    if patched is None:
        raise SystemExit(f"Texture {target_name or png_path.stem} not found in {bftex_path}.")

    if out_path is None or out_path == bftex_path:
        out_path = bftex_path
    out_path.write_bytes(patched)
    return out_path


def decompress_if_needed(raw: bytes) -> tuple[bytes, bool]:
    raw_bytes = bytes(raw)
    if raw_bytes.startswith(b"Yaz0"):
        return oead.yaz0.decompress(raw_bytes), True
    return raw_bytes, False


def rebuild_sarc_from(sarc: oead.Sarc, replacements: dict[str, bytes], compress: bool) -> bytes:
    writer = oead.SarcWriter.from_sarc(sarc)
    for name, data in replacements.items():
        writer.files[name] = data
    out_bytes = bytes(writer.write()[1])
    return oead.yaz0.compress(out_bytes) if compress else out_bytes


def patch_sarc_nameless(sarc_bytes: bytes, png_map: dict[str, Path]) -> tuple[bytes, bool, set[str]]:
    """Patch BNTX payloads in a SARC that may have empty filenames (hashed SFAT)."""
    data = bytearray(sarc_bytes)
    header_size = struct.unpack_from("<H", data, 0x04)[0]
    data_offset = struct.unpack_from("<I", data, 0x0C)[0]
    sfat_off = header_size
    node_count = struct.unpack_from("<H", data, sfat_off + 0x06)[0]
    node_table_off = sfat_off + 0x0C
    changed = False
    touched_all: set[str] = set()

    for i in range(node_count):
        entry_off = node_table_off + i * 0x10
        _, _, data_start, data_end = struct.unpack_from("<IIII", data, entry_off)
        start = data_offset + data_start
        end = data_offset + data_end
        file_bytes = bytes(data[start:end])
        if b"BNTX" not in file_bytes[:0x100]:
            continue

        patched, touched = patch_texture_bytes_multi(file_bytes, png_map)
        if patched:
            if len(patched) != len(file_bytes):
                raise SystemExit("Patched data size changed; cannot rewrite hashed SARC safely.")
            data[start:end] = patched
            changed = True
            touched_all.update(touched)

    return bytes(data), changed, touched_all


def patch_sarc_file(sarc_path: Path, png_map: dict[str, Path]) -> tuple[bool, set[str]]:
    raw = sarc_path.read_bytes()
    data, sarc_was_compressed = decompress_if_needed(raw)
    sarc = oead.Sarc(data)

    replacements_outer: dict[str, bytes] = {}
    patched_any = False
    touched_total: set[str] = set()

    for entry in sarc.get_files():
        name_lower = (entry.name or "").lower()
        inner_raw = entry.data
        inner_data, inner_was_compressed = decompress_if_needed(inner_raw)

        # Process only CharaIcon archives, including hashed/empty names if they contain __Combined.bntx
        should_process = False
        if "charaicon_00.szs" in name_lower:
            should_process = True
        elif not entry.name and b"__combined.bntx" in inner_data.lower():
            should_process = True

        if not should_process:
            continue

        patched_inner, inner_changed, touched_inner = patch_sarc_nameless(inner_data, png_map)

        if inner_changed:
            inner_bytes = oead.yaz0.compress(patched_inner) if inner_was_compressed else patched_inner
            replacements_outer[entry.name] = inner_bytes
            patched_any = True
            touched_total.update(touched_inner)

    if not patched_any:
        return False, set()

    new_sarc = rebuild_sarc_from(sarc, replacements_outer, sarc_was_compressed)
    sarc_path.write_bytes(new_sarc)
    return True, touched_total


def main() -> None:
    parser = argparse.ArgumentParser(description="Replace a BFTEX/BNTX texture with an RGBA PNG.")
    parser.add_argument("--png", type=Path, help="PNG whose pixels will be injected.")
    parser.add_argument("--target", type=str, help="Texture name to replace (defaults to PNG stem).")
    parser.add_argument("--bftex", type=Path, help="Standalone .bftex/.bntx to patch.")
    parser.add_argument(
        "--sarc",
        type=Path,
        nargs="*",
        help="One or more .sarc archives containing m_L_CharaIcon_00.szs/__Combined.bntx to patch in place.",
    )
    parser.add_argument(
        "--auto",
        action="store_true",
        help="Auto-mode: patch every PNG in --png-dir into matching textures across given/auto-detected SARCs.",
    )
    parser.add_argument(
        "--png-dir",
        type=Path,
        default=Path("test_textures"),
        help="Directory scanned for PNGs in --auto mode (default: test_textures).",
    )
    parser.add_argument(
        "--out",
        type=Path,
        help="Optional output path for --bftex mode. Default: overwrite the input (creates .bak once).",
    )
    args = parser.parse_args()

    if args.auto:
        png_dir = args.png_dir
        pngs = {p.stem: p for p in png_dir.glob("*.png")}
        if not pngs:
            raise SystemExit(f"Aucun PNG trouvé dans {png_dir}")
        sarc_paths = args.sarc or list(png_dir.glob("*.sarc"))
        if not sarc_paths:
            raise SystemExit("Aucun .sarc fourni ou trouvé en --auto.")
        worked = False
        for sarc_path in sarc_paths:
            changed, _ = patch_sarc_file(sarc_path, pngs)
            if changed:
                print(f"Patched {sarc_path}")
                worked = True
            else:
                print(f"Skipped {sarc_path}")
        if not worked:
            raise SystemExit("Nothing patched in --auto mode.")
        return

    if not args.png:
        raise SystemExit("Missing --png (or use --auto).")

    target_name = args.target or args.png.stem
    worked = False

    if args.bftex:
        out_path = patch_bftex_file(args.bftex, args.png, target_name, args.out)
        print(f"Wrote patched bftex to {out_path}")
        worked = True

    if args.sarc:
        png_map = {target_name: args.png}
        for sarc_path in args.sarc:
            changed, _ = patch_sarc_file(sarc_path, png_map)
            if changed:
                print(f"Patched {sarc_path}")
                worked = True
            else:
                print(f"Skipped {sarc_path}: texture {target_name} not found.")

    if not worked:
        raise SystemExit("Nothing patched. Provide --bftex or --sarc paths (or use --auto).")


if __name__ == "__main__":
    main()
