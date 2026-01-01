import os
import json
import math
import random
import shutil
import struct
import sys
from io import BytesIO
from pathlib import Path
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from PIL import Image, ImageTk

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from mk8dx_audio_classes import Bars, calculate_crc32_hash, pad_till

ICON = 64
TEXT_H = 28

GRID_COLS = 8
GRID_ROWS = 6
GRID_COUNT = GRID_COLS * GRID_ROWS

CONFIG_DIRNAME = "config"
MISSING_DIALOG_THRESHOLD = 1500  # avoid spawning thousands of checkboxes that freeze Tk

# Group cell settings: list of parents (index in grid) and slot counts
GROUP_DEFS = [
    {"index": 21, "size": 2},  # x=5, y=2
    {"index": 41, "size": 2},  # x=1, y=5
    {"index": 43, "size": 2},  # x=3, y=5
]

# Blocked cells (no drag/drop, blacked out)
BLOCKED_INDICES = {40, 47}  # x=0, y=5 and x=7, y=5 (bottom-right)

# Mapping file name (stored inside mods_characters_mk8dx)
MAPPING_FILENAME = "mapping.json"

# Fixed names per grid cell (excluding blocked)
CASE_NAME_BY_INDEX = {
    0: "Mario",
    1: "Luigi",
    2: "Peach",
    3: "Daisy",
    4: "Rosalina",
    5: "TanukiMario",
    6: "CatPeach",
    7: "DrvChr01",
    8: "Yoshi",
    9: "Kinopio",
    10: "Nokonoko",
    11: "Heyho",
    12: "Jugem",
    13: "Kinopico",
    14: "KingTeresa",
    15: "DrvChr03",
    16: "BbMario",
    17: "BbLuigi",
    18: "BbPeach",
    19: "BbDaisy",
    20: "BbRosalina",
    21: "GoldMario",
    22: "MetalPeach",
    23: "DrvChr04",
    24: "Wario",
    25: "Waluigi",
    26: "DK",
    27: "Koopa",
    28: "Karon",
    29: "KoopaJr",
    30: "HoneKoopa",
    31: "DrvChr02",
    32: "Lemmy",
    33: "Larry",
    34: "Wendy",
    35: "Ludwig",
    36: "Iggy",
    37: "Roy",
    38: "Morton",
    39: "DrvChr07",
    41: "AnimalBoyA",
    42: "Shizue",
    43: "LinkBotw",
    44: "DrvChr05",
    45: "DrvChr06",
    46: "DrvChr08",
}

# Fixed names per group slot
GROUP_SLOT_NAMES = {
    21: [
        "GoldMario",
        "MetalMario",
    ],
    41: [
        "AnimalBoyA",
        "AnimalGirlA",
    ],
    43: [
        "LinkBotw",
        "Link",
    ],
}

PREFIX = "tc_Chara_"
IMAGE_EXT = (".png", ".jpg", ".jpeg", ".bmp", ".gif", ".webp")

AUDIO_MAP_FILENAME = "audio_assets_map.json"
BFWAV_GROUPS_FILENAME = "bfwav_groups.json"
BASE_AUDIO_DIRNAME = "Audio"

def _config_path(base: Path, filename: str) -> Path:
    """Return path to a config file under config/, falling back to root."""
    config_dir = base / CONFIG_DIRNAME
    candidates = [config_dir / filename, base / filename]
    for cand in candidates:
        if cand.exists():
            return cand
    return candidates[0]


def _load_audio_map_at(base: Path):
    path = _config_path(base, AUDIO_MAP_FILENAME)
    if not path.exists():
        print(f"[ERROR] audio_assets_map.json not found at the root ({path})")
        return None
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"[ERROR] Failed to read audio_assets_map.json at {path}: {e}")
        return None


def _load_bfwav_groups_at(base: Path):
    path = _config_path(base, BFWAV_GROUPS_FILENAME)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    name_to_group = {}
    for grp in data.get("groups", []):
        members = set(grp)
        for name in members:
            name_to_group[name] = members
    return name_to_group


def _find_map_entry(audio_map, bars_path: str):
    """Return the config entry matching the selected .bars file."""
    name = Path(bars_path).name
    for section, entries in audio_map.items():
        if name in entries:
            entry = entries[name].copy()
            entry["section"] = section
            return entry
    return None


def _swap_prefix(name: str, src_prefix: str, dest_prefix: str) -> str:
    if src_prefix and dest_prefix and src_prefix in name:
        return name.replace(src_prefix, dest_prefix, 1)
    return name


def _asset_to_bytes(asset) -> bytes:
    buf = BytesIO()
    asset.write(buf)
    return buf.getvalue()


def _write_header_updates(dest_bytes: bytearray, bars_dest: Bars):
    """Rewrite size and asset_offsets in the header without touching metas."""
    endian = '>' if bars_dest.bom == '>' else '<'
    meta_count = bars_dest.meta_count

    # Mettre à jour la taille totale
    dest_bytes[4:8] = struct.pack(endian + 'I', len(dest_bytes))

    # Les paires (meta_offset, asset_offset) commencent après :
    # 4 (magic) + 4 (size) + 2 (bom) + 2 (version) + 4 (meta_count) + 4*meta_count (crc_hashes)
    table_start = 4 + 4 + 2 + 2 + 4 + (4 * meta_count)
    for idx in range(min(meta_count, len(bars_dest.asset_offsets))):
        asset_off = bars_dest.asset_offsets[idx] & 0xFFFFFFFF
        pos = table_start + idx * 8 + 4  # +4 pour viser la partie asset_offset de la paire
        dest_bytes[pos:pos + 4] = struct.pack(endian + 'I', asset_off)


def _transfer_bfwavs(bars_source: Bars, bars_dest: Bars, src_info, dest_info, name_to_group: dict[str, set], dest_bytes: bytearray):
    src_prefix = src_info.get("prefix", "")
    dest_prefix = dest_info.get("prefix", "")
    src_list = src_info.get("bfwav", []) or []
    dest_list = set(dest_info.get("bfwav", []) or [])

    src_crc_to_idx = {crc: idx for idx, crc in enumerate(bars_source.crc_hashes)}
    dest_crc_to_idx = {crc: idx for idx, crc in enumerate(bars_dest.crc_hashes)}

    # Build target -> candidate sources mapping
    target_to_sources = {}
    for src_name in src_list:
        # Priorite : mapping direct par prefixe (source -> dest)
        direct_target = _swap_prefix(src_name, src_prefix, dest_prefix)
        if direct_target in dest_list:
            target_to_sources.setdefault(direct_target, set()).add(src_name)
            continue
        # Sinon, fallback par groupe
        group = name_to_group.get(src_name, {src_name})
        targets = group & dest_list if dest_list else group
        for target in targets:
            target_to_sources.setdefault(target, set()).add(src_name)

    replaced = 0
    ignored = []

    for dest_name, sources in target_to_sources.items():
        # Choisir de preference une source dont le swap prefixe == dest_name
        def swapped_ok(n):
            return _swap_prefix(n, src_prefix, dest_prefix) == dest_name
        candidates = [n for n in sources if swapped_ok(n)]
        src_name = random.choice(candidates or list(sources))

        src_hash = calculate_crc32_hash(src_name)
        dest_hash = calculate_crc32_hash(dest_name)

        src_idx_resolved = src_crc_to_idx.get(src_hash)
        dest_idx = dest_crc_to_idx.get(dest_hash)

        if src_idx_resolved is None or src_idx_resolved >= len(bars_source.assets):
            ignored.append(dest_name)
            continue
        if dest_idx is None or dest_idx >= len(bars_dest.assets) or dest_idx >= len(bars_dest.metas):
            ignored.append(dest_name)
            continue

        # Offsets actuels (mise à jour après chaque insertion si besoin)
        start = bars_dest.asset_offsets[dest_idx]
        next_offsets = [o for o in bars_dest.asset_offsets if o > start]
        end = min(next_offsets) if next_offsets else len(dest_bytes)
        slot_size = end - start

        new_data = _asset_to_bytes(bars_source.assets[src_idx_resolved])
        new_size = pad_till(len(new_data))
        padded = new_data + b"\x00" * (new_size - len(new_data))

        size_diff = new_size - slot_size

        if size_diff <= 0:
            # Remplacement en place, garder la taille du slot pour ne pas toucher aux offsets
            dest_bytes[start:end] = padded + b"\x00" * (-size_diff)
        else:
            # Besoin d'agrandir : on insère et on decale les offsets suivants
            dest_bytes[start:end] = padded
            for i, off in enumerate(bars_dest.asset_offsets):
                if off > start:
                    bars_dest.asset_offsets[i] = off + size_diff
            bars_dest.size = len(dest_bytes)

        replaced += 1

    return replaced, ignored, dest_bytes


def _process_bars_pair(source_path: str, dest_path: str, audio_map, bfwav_groups=None, bars_cache=None):
    src_info = _find_map_entry(audio_map, source_path)
    dest_info = _find_map_entry(audio_map, dest_path)

    if not src_info or not dest_info:
        missing = source_path if not src_info else dest_path
        print(f"[ERROR] Could not find {Path(missing).name} in audio_assets_map.json")
        return None

    bars_cache = bars_cache if bars_cache is not None else {}
    bars_source = bars_cache.get(source_path)
    if bars_source is None:
        # Garder au plus une source .bars en memoire pour eviter un cache gigantesque
        if bars_cache:
            bars_cache.clear()
        print(f"Loading source: {source_path}")
        try:
            bars_source = Bars(source_path)
            bars_cache[source_path] = bars_source
        except Exception as e:
            print(f"[ERROR] Failed to read source ({source_path}): {e}")
            return None

    try:
        bars_dest = Bars(dest_path)
    except Exception as e:
        print(f"[ERROR] Failed to read destination ({dest_path}): {e}")
        return None
    dest_bytes = bytearray(Path(dest_path).read_bytes())

    bfwav_groups = bfwav_groups or {}
    replaced, ignored, dest_bytes = _transfer_bfwavs(
        bars_source, bars_dest, src_info, dest_info, bfwav_groups, dest_bytes
    )
    _write_header_updates(dest_bytes, bars_dest)
    print(f"Replacements done: {replaced}")
    if ignored:
        print(f"Ignored (missing in destination after prefix swap): {len(ignored)}")

    Path(dest_path).write_bytes(dest_bytes)
    return replaced, ignored


def extract_character_name(filename: str) -> str:
    # tc_Chara_<name>^l.png  -> <name>
    base = os.path.splitext(filename)[0]
    if not base.startswith(PREFIX):
        return ""
    base = base[len(PREFIX):]
    if "^" in base:
        base = base.split("^", 1)[0]
    return base


class ScrollableFrame(ttk.Frame):
    def __init__(self, master, width=300, height=550):
        super().__init__(master)

        self.canvas = tk.Canvas(self, width=width, height=height, highlightthickness=0)
        self.scroll = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.inner = ttk.Frame(self.canvas)

        self.inner_id = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.canvas.configure(yscrollcommand=self.scroll.set)

        self.canvas.pack(side="left", fill="both", expand=True)
        self.scroll.pack(side="right", fill="y")

        self.inner.bind("<Configure>", self._on_inner)
        self.canvas.bind("<Configure>", self._on_canvas)

        self.canvas.bind_all("<MouseWheel>", self._on_mousewheel, add="+")
        self.canvas.bind_all("<Button-4>", self._on_linux_scroll, add="+")
        self.canvas.bind_all("<Button-5>", self._on_linux_scroll, add="+")

    def _on_inner(self, _):
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _on_canvas(self, event):
        self.canvas.itemconfigure(self.inner_id, width=event.width)

    def _on_mousewheel(self, event):
        self.canvas.yview_scroll(int(-event.delta / 120), "units")

    def _on_linux_scroll(self, event):
        self.canvas.yview_scroll(-1 if event.num == 4 else 1, "units")


class MK8DXEditor(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("MK8DX Character Placement")

        self.characters = []  # {path,file,name,image}
        self.grid = [None] * GRID_COUNT

        # group slots keyed by parent index
        self.groups = {
            g["index"]: {
                "size": g["size"],
                "slots": [None] * g["size"]
            }
            for g in GROUP_DEFS
        }
        self.char_by_name = {}
        self.mod_root = self._guess_mod_root()  # mods_characters_mk8dx folder
        self.group_overlay = None
        self.group_overlay_parent = None
        self.group_pinned = False  # opened by click
        self.group_hover_open = False  # opened temporarily during drag hover

        # drag
        self.drag_char = None
        self.drag_source = None  # None | ("grid", idx) | ("group", parent_idx, gidx) | ("lib", None)
        self.drag_ghost = None

        self._build_ui()
        # prompt for mod root early
        self.after(100, self.load_characters)

    # ---------------- UI ----------------

    def _build_ui(self):
        toolbar = ttk.Frame(self, padding=6)
        toolbar.pack(fill="x")

        ttk.Button(toolbar, text="Load characters", command=self.load_characters).pack(side="left")
        ttk.Button(toolbar, text="Clear grid", command=self.clear_grid).pack(side="left", padx=6)
        ttk.Button(toolbar, text="Randomize grid", command=self.randomize_grid).pack(side="left", padx=6)
        ttk.Button(toolbar, text="Import preset", command=self.import_preset).pack(side="left", padx=18)
        ttk.Button(toolbar, text="Export preset", command=self.export_preset).pack(side="left")
        ttk.Button(toolbar, text="Copy files", command=self.copy_files).pack(side="left", padx=12)

        self.status = tk.StringVar(value="Select mods_characters_mk8dx to load thumbnails.")
        ttk.Label(toolbar, textvariable=self.status).pack(side="left", padx=18)

        main = ttk.Frame(self, padding=6)
        main.pack(fill="both", expand=True)

        # Library
        lib_frame = ttk.Frame(main)
        lib_frame.pack(side="left", fill="y")

        ttk.Label(lib_frame, text="Characters").pack(anchor="w")

        self.lib = ScrollableFrame(lib_frame, width=320, height=560)
        self.lib.pack(fill="y", pady=(6, 0))

        # Grid
        grid_frame = ttk.Frame(main, padding=(12, 0))
        grid_frame.pack(side="left")

        ttk.Label(grid_frame, text="8x6 grid").pack(anchor="w")

        self.grid_container = ttk.Frame(grid_frame)
        self.grid_container.pack(pady=(6, 0))

        self.grid_img_cells = []
        self.grid_text_cells = []
        self.grid_badges = []  # for parent badge

        # Build as 2-row per grid row: images row then text row
        for i in range(GRID_COUNT):
            r, c = divmod(i, GRID_COLS)

            # ---- Image cell container fixed 64x64
            img_cell = ttk.Frame(self.grid_container, width=ICON, height=ICON)
            img_cell.grid(row=r * 2, column=c, padx=3, pady=(3, 0))
            img_cell.grid_propagate(False)
            img_cell.idx = i

            # Label inside fixed size via place (pixels)
            img_label = tk.Label(img_cell, bd=1, relief="solid", bg=self.cget("bg"))
            img_label.place(x=0, y=0, width=ICON, height=ICON)
            img_label.idx = i  # attach idx for hit-testing

            # Badge (bottom-right). Only used for group parent.
            badge = tk.Label(
                img_cell,
                text="",
                font=("Arial", 9, "bold"),
                bg="white",
                fg="black",
                bd=0
            )
            # place it; hide by default
            badge.place_forget()

            self.grid_badges.append(badge)

            # Start drag from grid (or click parent)
            img_label.bind("<ButtonPress-1>", self._on_grid_press)

            # ---- Text cell fixed height, same width as icon
            txt_cell = ttk.Frame(self.grid_container, width=ICON, height=TEXT_H)
            txt_cell.grid(row=r * 2 + 1, column=c, padx=3, pady=(0, 3))
            txt_cell.grid_propagate(False)

            txt_label = tk.Label(txt_cell, text="", font=("Arial", 8), bg=self.cget("bg"))
            txt_label.place(x=0, y=0, width=ICON, height=TEXT_H)

            self.grid_img_cells.append(img_label)
            self.grid_text_cells.append(txt_label)

        # Global bindings (bind_all so drag from overlay works too)
        self.bind_all("<B1-Motion>", self._on_drag_motion)
        self.bind_all("<ButtonRelease-1>", self._on_drag_release)
        self.bind("<ButtonPress-1>", self._on_global_click, add="+")

        # initial render for parent badges
        for parent_idx in self.groups:
            self.render_cell(parent_idx)
        # render blocked cells
        for bidx in BLOCKED_INDICES:
            self.render_cell(bidx)

    # ---------------- Characters ----------------

    def _guess_mod_root(self):
        candidates = []
        for cand in [SCRIPT_DIR, SCRIPT_DIR.parent, Path.cwd(), Path.cwd().parent]:
            try:
                if cand:
                    candidates.append(cand)
            except Exception:
                pass

        seen = set()
        for base in candidates:
            base = base.resolve()
            if base in seen:
                continue
            seen.add(base)
            mapping_path = _config_path(base, MAPPING_FILENAME)
            if (base / "characters").is_dir() and mapping_path.is_file():
                return str(base)
        return None

    def _ensure_mod_root(self):
        if self.mod_root and os.path.isdir(self.mod_root):
            return True
        guessed = self._guess_mod_root()
        if guessed:
            self.mod_root = guessed
            return True
        path = filedialog.askdirectory(title="mods_characters_mk8dx folder")
        if not path:
            return False
        self.mod_root = path
        return True

    def load_characters(self):
        if not self._ensure_mod_root():
            return

        self.characters.clear()
        for w in self.lib.inner.winfo_children():
            w.destroy()

        self.mapping = {}
        self._load_mapping()

        missing_icons = []
        self.char_by_name = {}

        chars_root = Path(self.mod_root) / "characters"
        subdirs = [p for p in chars_root.iterdir() if p.is_dir()]

        # build expected filename sets from mapping
        expected_by_dir = self._expected_files_by_dir()
        expected_driver = expected_by_dir.get("Driver", set())
        expected_audio_menu = expected_by_dir.get("Audio/DriverMenu", set())
        expected_audio_driver = expected_by_dir.get("Audio/Driver", set())
        expected_ui = expected_by_dir.get("UI/cmn", set())

        def folder_structure_ok(folder: Path):
            required = [
                folder / "Driver",
                folder / "Audio" / "DriverMenu",
                folder / "Audio" / "Driver",
                folder / "UI" / "cmn"
            ]
            return all(p.is_dir() for p in required)

        for path in sorted(subdirs, key=lambda p: p.name.lower()):
            name = path.name
            if not folder_structure_ok(path):
                print(f"WARNING: Folder skipped (missing structure): {name}")
                continue

            cmn_dir = path / "UI" / "cmn"
            icon_path = None
            for f in sorted(cmn_dir.iterdir()):
                if f.is_file() and f.name.lower().startswith("tc_chara") and f.suffix.lower() in IMAGE_EXT:
                    icon_path = f
                    break

            # V?rrifie si au moins un fichier attendu par mapping existe
            def has_expected(expected_set, subdir):
                return any((subdir / fname).is_file() for fname in expected_set)

            if not (
                has_expected(expected_driver, path / "Driver")
                or has_expected(expected_audio_menu, path / "Audio" / "DriverMenu")
                or has_expected(expected_audio_driver, path / "Audio" / "Driver")
                or has_expected(expected_ui, cmn_dir)
            ):
                print(f"WARNING: Folder skipped (no expected mapping files): {name}")
                continue

            tkimg = None
            if icon_path and icon_path.is_file():
                try:
                    img = Image.open(icon_path).convert("RGBA")
                    img = img.resize((ICON, ICON), Image.Resampling.LANCZOS)
                    tkimg = ImageTk.PhotoImage(img)
                except Exception:
                    missing_icons.append(name)
            else:
                missing_icons.append(name)

            char = {"path": str(icon_path) if icon_path else "", "file": name, "name": name, "image": tkimg}
            self.characters.append(char)
            self.char_by_name[name] = char

            row = ttk.Frame(self.lib.inner, padding=4)
            row.pack(fill="x")

            lbl_img = tk.Label(row, image=tkimg if tkimg else "")
            if tkimg:
                lbl_img.image = tkimg
            lbl_img.pack(side="left", padx=(0, 6))

            lbl_txt = ttk.Label(row, text=name)
            lbl_txt.pack(side="left", padx=8)

            for w in (row, lbl_img, lbl_txt):
                w.bind("<ButtonPress-1>", lambda e, c=char: self._start_drag(c, ("lib", None)))

        # start with empty placement; user associe manuellement
        self.clear_grid()

        msg = f"{len(self.characters)} icons loaded from {chars_root}"
        if missing_icons:
            msg += f" (missing/corrupted icons for: {', '.join(sorted(set(missing_icons))[:6])}"
            if len(missing_icons) > 6:
                msg += "..."
            msg += ")"
        self.status.set(msg)

        dup_report = self._dup_process_base_folder(Path(chars_root))
        print(dup_report)
        self.status.set(f"{msg} | Folder duplication check: ok")

    def _apply_fixed_layout(self):
        def pick_default(name_hint=None):
            if name_hint and name_hint in self.char_by_name:
                return self.char_by_name[name_hint]
            for ch in self.characters:
                if ch.get("image") is not None:
                    return ch
            return self.characters[0] if self.characters else None

        # set grid cases to fixed mapping (ignore blocked)
        for idx in range(GRID_COUNT):
            if idx in BLOCKED_INDICES:
                self.grid[idx] = None
                continue
            name = CASE_NAME_BY_INDEX.get(idx)
            self.grid[idx] = pick_default(name)

        # set group slots
        for parent_idx, group in self.groups.items():
            names = GROUP_SLOT_NAMES.get(parent_idx, [])
            for gi in range(group["size"]):
                nm = names[gi] if gi < len(names) else None
                group["slots"][gi] = pick_default(nm)

        for i in range(GRID_COUNT):
            self.render_cell(i)
        if self.group_overlay is not None:
            self._render_group_overlay()

    # ---------------- Group overlay ----------------

    def _group_dims(self, size: int):
        # approximate square layout
        cols = max(1, int(math.ceil(math.sqrt(size))))
        rows = int(math.ceil(size / cols))
        return rows, cols

    def _show_group_overlay(self, parent_idx: int):
        # switch overlay to another parent if needed
        if self.group_overlay is not None and self.group_overlay_parent != parent_idx:
            self._hide_group_overlay()
        if self.group_overlay is not None:
            return

        parent_lbl = self.grid_img_cells[parent_idx]
        x = parent_lbl.winfo_rootx()
        y = parent_lbl.winfo_rooty()

        ov = tk.Toplevel(self)
        ov.overrideredirect(True)
        ov.attributes("-topmost", True)
        ov.geometry(f"+{x}+{y}")

        # Cadre avec contour visible autour du groupe
        outer = tk.Frame(ov, bd=2, relief="solid", bg=self.cget("bg"))
        outer.pack(padx=2, pady=2)

        container = ttk.Frame(outer, padding=2)
        container.pack()

        self.group_img_cells = []
        self.group_text_cells = []

        size = self.groups[parent_idx]["size"]
        rows, cols = self._group_dims(size)

        for gi in range(size):
            gr, gc = divmod(gi, cols)

            # image frame 64x64
            img_cell = ttk.Frame(container, width=ICON, height=ICON)
            img_cell.grid(row=gr * 2, column=gc, padx=2, pady=(2, 0))
            img_cell.grid_propagate(False)

            img_label = tk.Label(img_cell, bd=1, relief="solid", bg=self.cget("bg"))
            img_label.place(x=0, y=0, width=ICON, height=ICON)
            img_label.gidx = gi
            img_label.parent_idx = parent_idx

            # text frame 64x16
            txt_cell = ttk.Frame(container, width=ICON, height=TEXT_H)
            txt_cell.grid(row=gr * 2 + 1, column=gc, padx=2, pady=(0, 2))
            txt_cell.grid_propagate(False)

            txt_label = tk.Label(txt_cell, text="", font=("Arial", 8), bg=self.cget("bg"))
            txt_label.gidx = gi
            txt_label.parent_idx = parent_idx
            txt_label.bind("<ButtonPress-1>", self._on_group_press)

            txt_label.place(x=0, y=0, width=ICON, height=TEXT_H)

            img_label.bind("<ButtonPress-1>", self._on_group_press)

            self.group_img_cells.append(img_label)
            self.group_text_cells.append(txt_label)

        self.group_overlay_parent = parent_idx
        self.group_overlay = ov
        self.group_hover_open = not self.group_pinned
        self._render_group_overlay()

    def _hide_group_overlay(self):
        if self.group_overlay is None:
            return
        try:
            self.group_overlay.destroy()
        except Exception:
            pass
        self.group_overlay = None
        self.group_overlay_parent = None
        self.group_img_cells = []
        self.group_text_cells = []
        self.group_hover_open = False

    def _render_group_overlay(self):
        if self.group_overlay is None or self.group_overlay_parent is None:
            return
        slots = self.groups[self.group_overlay_parent]["slots"]
        for gi, (img_lbl, txt_lbl) in enumerate(zip(self.group_img_cells, self.group_text_cells)):
            ch = slots[gi]
            if ch is None:
                img_lbl.config(image="", bg=self.cget("bg"))
                img_lbl.image = None
                txt_lbl.config(text="", bg=self.cget("bg"))
            else:
                img_lbl.config(image=ch["image"], bg="white")
                img_lbl.image = ch["image"]
                txt_lbl.config(text=ch["name"], bg=self.cget("bg"))

    # ---------------- Drag & drop ----------------

    def _start_drag(self, char, source):
        if char is None:
            return
        self.drag_char = char
        self.drag_source = source

        if self.drag_ghost:
            try:
                self.drag_ghost.destroy()
            except Exception:
                pass

        self.drag_ghost = tk.Toplevel(self)
        self.drag_ghost.overrideredirect(True)
        self.drag_ghost.attributes("-topmost", True)

        lbl = tk.Label(self.drag_ghost, image=char["image"], bd=0)
        lbl.pack()
        lbl.image = char["image"]

        self._move_ghost()

    def _on_grid_press(self, event):
        idx = event.widget.idx

        if idx in BLOCKED_INDICES:
            return

        # Clicking parent toggles pinned overlay (if not dragging something)
        if idx in self.groups:
            if self.group_overlay is None or self.group_overlay_parent != idx:
                self.group_pinned = True
                self._show_group_overlay(idx)
            else:
                # if open and pinned -> close
                if self.group_pinned:
                    self.group_pinned = False
                    self._hide_group_overlay()
                else:
                    # open (hover) -> pin it
                    self.group_pinned = True
            return

        char = self.grid[idx]
        if char:
            self._start_drag(char, ("grid", idx))

    def _on_group_press(self, event):
        gi = event.widget.gidx
        parent_idx = event.widget.parent_idx
        ch = self.groups[parent_idx]["slots"][gi]
        if ch:
            self._start_drag(ch, ("group", parent_idx, gi))


    def _move_ghost(self):
        if not self.drag_ghost:
            return
        x = self.winfo_pointerx() + 12
        y = self.winfo_pointery() + 12
        self.drag_ghost.geometry(f"+{x}+{y}")

    def _on_drag_motion(self, _):
        if self.drag_char:
            self._move_ghost()

            # hover-open group overlay when dragging over a parent cell
            parent_idx = self._parent_under_pointer()
            if parent_idx is not None and not self.group_pinned:
                if self.group_overlay is None or self.group_overlay_parent != parent_idx:
                    self.group_hover_open = True
                    self._show_group_overlay(parent_idx)
            else:
                if self.group_overlay is not None and not self.group_pinned and self.group_hover_open:
                    if not self._pointer_over_overlay():
                        self._hide_group_overlay()

    def _on_drag_release(self, _):
        if not self.drag_char:
            return

        # Si le curseur est au-dessus du ghost, winfo_containing renvoie un widget du ghost.
        # On va donc ignorer le ghost en testant l'appartenance.
        wx, wy = self.winfo_pointerx(), self.winfo_pointery()
        target = self.winfo_containing(wx, wy)

        def is_child_of(widget, ancestor):
            t = widget
            while t is not None:
                if t == ancestor:
                    return True
                t = getattr(t, "master", None)
            return False

        if self.drag_ghost is not None and target is not None:
            if is_child_of(target, self.drag_ghost):
                # On est sur le ghost : essayer de recuperer la vraie cible derrière.
                # Astuce simple : d?caler un tout petit peu le point.
                target = self.winfo_containing(wx - 10, wy - 10)

        # ---- Detect group subcell target (image label) ----
        target_gi = None
        target_parent_idx = None
        if self.group_overlay is not None:
            t = target
            while t is not None:
                if hasattr(t, "gidx") and t in getattr(self, "group_img_cells", []):
                    target_gi = t.gidx
                    target_parent_idx = getattr(t, "parent_idx", self.group_overlay_parent)
                    break
                t = t.master

        # ---- Detect grid target ----
        target_idx = None
        t = target
        while t is not None:
            if hasattr(t, "idx") and t in self.grid_img_cells:
                target_idx = t.idx
                break
            t = t.master

        # If drop on group subcell
        if target_gi is not None and target_parent_idx is not None:
            self._drop_to_group(target_parent_idx, target_gi)

        # Else drop on normal grid cell (exclude parents/blocked)
        elif target_idx is not None and target_idx not in self.groups and target_idx not in BLOCKED_INDICES:
            self._drop_to_grid(target_idx)

        else:
            # drop outside => if source is grid/group, remove
            if self.drag_source and self.drag_source[0] in ("grid", "group"):
                self._remove_from_source()

        # Cleanup ghost
        if self.drag_ghost:
            try:
                self.drag_ghost.destroy()
            except Exception:
                pass

        self.drag_char = None
        self.drag_source = None
        self.drag_ghost = None

        # close hover overlay if not pinned
        if self.group_overlay is not None and not self.group_pinned and self.group_hover_open:
            if not self._pointer_over_overlay_or_parent():
                self._hide_group_overlay()


    def _drop_to_grid(self, target_idx: int):
        src = self.drag_source
        if target_idx in BLOCKED_INDICES:
            return

        # lib -> grid : replace
        if src[0] == "lib":
            self.grid[target_idx] = self.drag_char
            self.render_cell(target_idx)
            return

        # grid -> grid : swap
        if src[0] == "grid":
            src_idx = src[1]
            if src_idx == target_idx:
                return
            self.grid[src_idx], self.grid[target_idx] = self.grid[target_idx], self.grid[src_idx]
            self.render_cell(src_idx)
            self.render_cell(target_idx)
            return

        # group -> grid : swap between group slot and grid cell
        if src[0] == "group":
            src_parent = src[1]
            gi = src[2]
            slots = self.groups[src_parent]["slots"]
            slots[gi], self.grid[target_idx] = self.grid[target_idx], slots[gi]
            if self.group_overlay_parent == src_parent:
                self._render_group_overlay()
            self.render_cell(target_idx)
            self.render_cell(src_parent)
            return

    def _drop_to_group(self, parent_idx: int, target_gi: int):
        src = self.drag_source
        slots = self.groups[parent_idx]["slots"]

        # lib -> group : replace
        if src[0] == "lib":
            slots[target_gi] = self.drag_char
            if self.group_overlay_parent == parent_idx:
                self._render_group_overlay()
            self.render_cell(parent_idx)
            return

        # group -> group : swap
        if src[0] == "group":
            src_parent = src[1]
            src_gi = src[2]
            if src_parent == parent_idx and src_gi == target_gi:
                return
            src_slots = self.groups[src_parent]["slots"]
            src_slots[src_gi], slots[target_gi] = slots[target_gi], src_slots[src_gi]
            if self.group_overlay_parent == parent_idx or self.group_overlay_parent == src_parent:
                self._render_group_overlay()
            self.render_cell(src_parent)
            self.render_cell(parent_idx)
            return

        # grid -> group : swap between grid cell and group slot
        if src[0] == "grid":
            src_idx = src[1]
            self.grid[src_idx], slots[target_gi] = slots[target_gi], self.grid[src_idx]
            self.render_cell(src_idx)
            if self.group_overlay_parent == parent_idx:
                self._render_group_overlay()
            self.render_cell(parent_idx)
            return

    def _remove_from_source(self):
        src = self.drag_source
        if src[0] == "grid":
            idx = src[1]
            self.grid[idx] = None
            self.render_cell(idx)
        elif src[0] == "group":
            parent_idx = src[1]
            gi = src[2]
            self.groups[parent_idx]["slots"][gi] = None
            if self.group_overlay_parent == parent_idx:
                self._render_group_overlay()
            self.render_cell(parent_idx)

    def _parent_under_pointer(self):
        wx, wy = self.winfo_pointerx(), self.winfo_pointery()
        target = self.winfo_containing(wx, wy)

        t = target
        while t is not None:
            if hasattr(t, "idx") and t in self.grid_img_cells:
                idx = t.idx
                if idx in self.groups:
                    return idx
            t = getattr(t, "master", None)
        return None

    def _pointer_over_overlay(self) -> bool:
        if self.group_overlay is None:
            return False
        wx, wy = self.winfo_pointerx(), self.winfo_pointery()
        target = self.winfo_containing(wx, wy)

        t = target
        while t is not None:
            if t == self.group_overlay:
                return True
            t = getattr(t, "master", None)
        return False

    def _pointer_over_overlay_or_parent(self) -> bool:
        parent_idx = self.group_overlay_parent
        if parent_idx is None:
            return False
        if self._pointer_over_overlay():
            return True
        return self._parent_under_pointer() == parent_idx

    def _cell_label_text(self, fixed_name, char):
        current = char["name"] if char else ""
        if fixed_name and current:
            if fixed_name == current:
                return fixed_name
            return f"{fixed_name}\n{current}"
        if fixed_name:
            return f"{fixed_name}\n-"
        return current or ""

    def _on_global_click(self, event):
        # If pinned overlay open, click outside parent+overlay => close
        if self.group_overlay is None or not self.group_pinned:
            return

        wx, wy = self.winfo_pointerx(), self.winfo_pointery()
        target = self.winfo_containing(wx, wy)

        # click inside overlay?
        t = target
        while t is not None:
            if t == self.group_overlay:
                return
            t = t.master

        # click on parent?
        parent_idx = self.group_overlay_parent
        t = target
        while t is not None:
            if parent_idx is not None and hasattr(t, "idx") and t in self.grid_img_cells and t.idx == parent_idx:
                return
            t = t.master

        # else close
        self.group_pinned = False
        self._hide_group_overlay()

    # ---------------- Grid rendering ----------------

    def render_cell(self, idx):
        img_lbl = self.grid_img_cells[idx]
        txt_lbl = self.grid_text_cells[idx]

        if idx in BLOCKED_INDICES:
            img_lbl.config(image="", bg="black")
            img_lbl.image = None
            txt_lbl.config(text="", bg="black")
            self.grid_badges[idx].place_forget()
            return

        # Special rendering for group parent
        if idx in self.groups:
            # Parent shows first subcase (slot 0) at rest
            slots = self.groups[idx]["slots"]
            ch = slots[0] if slots else None
            if ch is None:
                img_lbl.config(image="", bg=self.cget("bg"))
                img_lbl.image = None
                txt_lbl.config(text=self._cell_label_text(CASE_NAME_BY_INDEX.get(idx), None), bg=self.cget("bg"))
            else:
                img_lbl.config(image=ch["image"], bg="white")
                img_lbl.image = ch["image"]
                txt_lbl.config(text=self._cell_label_text(CASE_NAME_BY_INDEX.get(idx), ch), bg=self.cget("bg"))

            # badge "9" bottom-right
            badge = self.grid_badges[idx]
            badge.config(text=str(self.groups[idx]["size"]))
            # small badge with a bit of padding
            badge.place(x=ICON - 16, y=ICON - 16, width=16, height=16)
            return

        # Normal cells
        char = self.grid[idx]
        if char is None:
            img_lbl.config(image="", bg=self.cget("bg"))
            img_lbl.image = None
            txt_lbl.config(text=self._cell_label_text(CASE_NAME_BY_INDEX.get(idx), None), bg=self.cget("bg"))
        else:
            img_lbl.config(image=char["image"], bg="white")
            img_lbl.image = char["image"]
            txt_lbl.config(text=self._cell_label_text(CASE_NAME_BY_INDEX.get(idx), char), bg=self.cget("bg"))

        # hide badge for non-parent
        self.grid_badges[idx].place_forget()

    def clear_grid(self):
        self.grid = [None] * GRID_COUNT
        for g in self.groups.values():
            g["slots"] = [None] * g["size"]
        for i in range(GRID_COUNT):
            self.render_cell(i)
        if self.group_overlay is not None:
            self._render_group_overlay()

    def randomize_grid(self):
        if not self.characters:
            messagebox.showerror("Error", "Load characters first.")
            return

        used_paths = set()

        def pick_for_slot():
            available = [ch for ch in self.characters if ch["path"] not in used_paths]
            if available:
                pick = random.choice(available)
                used_paths.add(pick["path"])
                return pick
            # no unique left -> take any
            return random.choice(self.characters)

        parent_indices = set(self.groups.keys())
        blocked_indices = set(BLOCKED_INDICES)

        # randomize normal grid cells (exclude parent/blocked indices)
        for i in range(GRID_COUNT):
            if i in parent_indices or i in blocked_indices:
                continue
            self.grid[i] = pick_for_slot()
            self.render_cell(i)

        # randomize group slots too
        for parent_idx, group in self.groups.items():
            for gi in range(group["size"]):
                group["slots"][gi] = pick_for_slot()
            self.render_cell(parent_idx)

        if self.group_overlay is not None:
            self._render_group_overlay()


    # ---------------- Files copy ----------------

    def _all_fixed_names(self):
        names = set(CASE_NAME_BY_INDEX.values())
        for lst in GROUP_SLOT_NAMES.values():
            names.update(lst)
        return names

    def _mapping_path(self):
        if not self.mod_root:
            return None
        base = Path(self.mod_root)
        return _config_path(base, MAPPING_FILENAME)

    def _default_files_for(self, name):
        return {
            "Driver": f"Driver/{name}.szs",
            "Audio/DriverMenu": f"Audio/DriverMenu/MenuDriver_{name}.bars",
            "Audio/Driver": f"Audio/Driver/Driver_{name}.bars",
            "ui": f"tc_Chara_{name}^l.png",
            "ui_ed": f"tc_edChara_{name}^l.png",
            "ui_map": f"tc_MapChara_{name}^l.png",
        }

    def _build_default_mapping(self):
        mapping = {}
        for name in sorted(self._all_fixed_names()):
            mapping[name] = self._default_files_for(name)
        return mapping

    def _expected_files_by_dir(self) -> dict[str, set[str]]:
        """Return expected file names grouped by relative directory, based on the mapping."""
        expected: dict[str, set[str]] = {
            "Driver": set(),
            "Audio/DriverMenu": set(),
            "Audio/Driver": set(),
            "UI/cmn": set(),
        }

        for entry in self.mapping.values():
            if not isinstance(entry, dict):
                continue

            def add_from_path(field_key: str, path_value: str):
                if not path_value:
                    return
                directory, fname = os.path.split(path_value)
                if not directory:
                    return
                expected.setdefault(directory, set()).add(fname)

            add_from_path("Driver", entry.get("Driver") or entry.get("driver", ""))
            add_from_path("Audio/DriverMenu", entry.get("Audio/DriverMenu") or entry.get("menu", ""))
            add_from_path("Audio/Driver", entry.get("Audio/Driver") or entry.get("audio", ""))

            # UI files are still just file names; directory is fixed.
            for ui_key in ("ui", "ui_ed", "ui_map"):
                fname = entry.get(ui_key)
                if fname:
                    expected["UI/cmn"].add(fname)

        # Drop empty dirs if nothing expected.
        return {k: v for k, v in expected.items() if v}

    def _normalize_mapping_entry(self, key, val):
        base = self._default_files_for(key)

        def _normalize_path(target_field, path_value):
            if not path_value:
                return ""
            normalized = str(path_value).replace("\\", "/")
            # Old format had only file names (no folder). Re-add default folder if missing.
            has_sep = "/" in normalized
            if not has_sep and target_field == "Driver":
                normalized = f"Driver/{normalized}"
            elif not has_sep and target_field == "Audio/DriverMenu":
                normalized = f"Audio/DriverMenu/{normalized}"
            elif not has_sep and target_field == "Audio/Driver":
                normalized = f"Audio/Driver/{normalized}"
            return normalized

        if isinstance(val, dict):
            merged = base.copy()
            alias = {
                "driver": "Driver",
                "menu": "Audio/DriverMenu",
                "audio": "Audio/Driver",
            }
            for k, v in val.items():
                if not v:
                    continue
                target_field = alias.get(k, k)
                if target_field in base:
                    if target_field in ("Driver", "Audio/DriverMenu", "Audio/Driver"):
                        merged[target_field] = _normalize_path(target_field, v)
                    else:
                        merged[target_field] = v
            return merged
        return base

    def _load_mapping(self):
        self.mapping = {}
        path = self._mapping_path()
        if path and path.is_file():
            try:
                with path.open("r", encoding="utf-8") as f:
                    raw = json.load(f)
                if isinstance(raw, dict):
                    for k, v in raw.items():
                        self.mapping[k] = self._normalize_mapping_entry(k, v)
            except Exception:
                self.mapping = {}
        if not self.mapping:
            self.mapping = self._build_default_mapping()

    def _save_mapping(self):
        path = self._mapping_path()
        if not path:
            return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("w", encoding="utf-8") as f:
                json.dump(self.mapping, f, indent=2, ensure_ascii=False)
        except Exception:
            pass

    # -------- duplication (character_copy) -------
    def _dup_list_files(self, folder: Path) -> list[str]:
        return sorted([p.name for p in folder.iterdir() if p.is_file()])

    def _confirm_missing_copy(self, title: str, summary: str, actions: list[dict]) -> list[dict]:
        """
        Custom dialog listing all missing files with checkboxes.
        Returns the subset of actions the user kept checked.
        """
        total = len(actions)
        if total == 0:
            return []

        # Large lists (thousands of files) make Tk hang when creating one checkbox per item.
        if total > MISSING_DIALOG_THRESHOLD:
            base = Path(self.mod_root) if getattr(self, "mod_root", None) else Path.cwd()
            log_path = base / "missing_files.txt"
            try:
                with log_path.open("w", encoding="utf-8") as f:
                    for action in actions:
                        kind = action.get("kind", "copy")
                        label = action.get("label", "")
                        src = action.get("src", "")
                        dst = action.get("dst", "")
                        f.write(f"[{kind}] {label} | src={src} | dst={dst}\n")
                print(f"[INFO] Full list of missing files written to {log_path}")
            except Exception as e:
                log_path = None
                print(f"[WARN] Could not write missing files log: {e}")

            msg = (
                f"{total} missing file(s) detected. The list is too large for a detailed selection.\n\n"
                "Copy/patch all files? (can take up to 5 hours for all .bars files, otherwise a few seconds)"
            )
            if log_path:
                msg += f"\n\nList saved in:\n{log_path}"
            if messagebox.askyesno(title, msg, parent=self, icon="warning"):
                return actions
            return []

        win = tk.Toplevel(self)
        win.title(title)
        win.transient(self)
        win.grab_set()
        win.geometry("+{}+{}".format(self.winfo_rootx() + 80, self.winfo_rooty() + 60))

        ttk.Label(win, text=summary, padding=(10, 8)).pack(anchor="w")

        outer = ttk.Frame(win, padding=(10, 0, 10, 8))
        outer.pack(fill="both", expand=True)

        canvas = tk.Canvas(outer, height=320)
        vsb = ttk.Scrollbar(outer, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=vsb.set)
        canvas.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")

        inner = ttk.Frame(canvas)
        inner_id = canvas.create_window((0, 0), window=inner, anchor="nw")

        vars_checked = []
        for action in actions:
            var = tk.BooleanVar(value=True)
            chk = ttk.Checkbutton(inner, text=action["label"], variable=var)
            chk.pack(anchor="w", pady=1)
            vars_checked.append((var, action))

        def _on_configure(_):
            canvas.configure(scrollregion=canvas.bbox("all"))
        inner.bind("<Configure>", _on_configure)

        def _on_canvas_configure(event):
            canvas.itemconfigure(inner_id, width=event.width)
        canvas.bind("<Configure>", _on_canvas_configure)

        btn_frame = ttk.Frame(win, padding=(10, 0, 10, 10))
        btn_frame.pack(fill="x")

        def select_all(val: bool):
            for var, _ in vars_checked:
                var.set(val)

        ttk.Button(btn_frame, text="Check all", command=lambda: select_all(True)).pack(side="left", padx=(0, 6))
        ttk.Button(btn_frame, text="Uncheck all", command=lambda: select_all(False)).pack(side="left", padx=(0, 18))

        result = {"selected": None}

        def on_yes():
            selected = [action for var, action in vars_checked if var.get()]
            result["selected"] = selected
            win.destroy()

        def on_no():
            result["selected"] = []
            win.destroy()

        ttk.Button(btn_frame, text="Copy selection", command=on_yes).pack(side="right", padx=(6, 0))
        ttk.Button(btn_frame, text="Cancel", command=on_no).pack(side="right")

        win.protocol("WM_DELETE_WINDOW", on_no)
        self.wait_window(win)
        return result["selected"] if result["selected"] is not None else []

    def _dup_process_single(self, child_dirs: list[Path], relative: str, expected_files: set[str], lines: list[str], missing_tasks: list):
        if not expected_files:
            lines.append(f"[{relative}] no expected files (empty mapping).")
            return

        missing_entries = []

        for child in child_dirs:
            folder = child / relative
            if not folder.is_dir():
                lines.append(f"[{relative}] {child.name}: missing subfolder, skipped")
                continue
            files = self._dup_list_files(folder)
            missing = expected_files.difference(files)
            if not missing:
                continue
            template_name = files[0] if files else None
            missing_entries.append((child, folder, missing, template_name))

        if not missing_entries:
            lines.append(f"[{relative}] no missing files.")
            return

        total_missing = sum(len(entry[2]) for entry in missing_entries)
        lines.append(
            f"[{relative}] {len(missing_entries)} incomplete folder(s), {total_missing} missing file(s) detected."
        )
        # Add to global tasks; confirmation happens once later.
        missing_tasks.append(("copy", relative, missing_entries))

    def _collect_audio_actions(self, child_dirs: list[Path], relative: str, expected_files: set[str], lines: list[str]) -> list[dict]:
        actions: list[dict] = []
        if not expected_files:
            lines.append(f"[{relative}] no expected files (empty mapping).")
            return actions

        missing_entries = []

        for child in child_dirs:
            folder = child / relative
            if not folder.is_dir():
                lines.append(f"[{relative}] {child.name}: missing subfolder, skipped")
                continue
            files = self._dup_list_files(folder)
            missing = expected_files.difference(files)
            if not missing:
                continue
            existing_bars = [f for f in files if f.lower().endswith(".bars")]
            src_name = existing_bars[0] if existing_bars else None
            missing_entries.append((child, folder, missing, src_name))

        if not missing_entries:
            lines.append(f"[{relative}] no missing files.")
            return actions

        total_missing = sum(len(entry[2]) for entry in missing_entries)
        lines.append(
            f"[{relative}] {len(missing_entries)} incomplete folder(s), {total_missing} missing file(s) detected."
        )

        for child, folder, missing, src_name in missing_entries:
            if not src_name:
                lines.append(f"[{relative}] {child.name}: no source .bars, nothing patched.")
                continue
            src_path = folder / src_name
            for name in sorted(missing):
                dest = folder / name
                label = f"{child.name}/{relative}/{name}"
                actions.append({"kind": "bars", "src": src_path, "dst": dest, "label": label, "relative": relative})

        return actions

    def _dup_process_base_folder(self, base_folder: Path) -> str:
        if not base_folder.exists():
            return f"Path not found: {base_folder}"
        child_dirs = sorted([p for p in base_folder.iterdir() if p.is_dir()], key=lambda p: p.name.lower())
        if not child_dirs:
            return "No subfolders found."
        lines: list[str] = [f"Parent folder: {base_folder}"]
        expected_by_dir = self._expected_files_by_dir()
        missing_tasks: list = []
        actions: list[dict] = []

        # Driver / UI
        for relative in ["Driver", "UI/cmn"]:
            expected_files = expected_by_dir.get(relative, set())
            self._dup_process_single(child_dirs, relative, expected_files, lines, missing_tasks)
            lines.append("")

        # Audio (.bars)
        for relative in ["Audio/DriverMenu", "Audio/Driver"]:
            expected_files = expected_by_dir.get(relative, set())
            actions.extend(self._collect_audio_actions(child_dirs, relative, expected_files, lines))
            lines.append("")

        # Actions issues de Driver/UI
        for kind, relative, entries in missing_tasks:
            if kind != "copy":
                continue
            for child, folder, missing, template_name in entries:
                if not template_name:
                    lines.append(f"[{relative}] {child.name}: no source file, nothing copied.")
                    continue
                template_path = folder / template_name
                for name in sorted(missing):
                    dest = folder / name
                    label = f"{child.name}/{relative}/{name}"
                    actions.append({"kind": "copy", "src": template_path, "dst": dest, "label": label})

        if not actions:
            return "\n".join(lines).rstrip()

        summary = f"{len(actions)} missing file(s) detected. Select which ones to copy."
        selected_actions = self._confirm_missing_copy("Complete missing files", summary, actions)

        if not selected_actions:
            lines.append("Copy canceled (no selection).")
            return "\n".join(lines).rstrip()

        # Executer les copies selectionnees
        copied_count = 0
        patched_count = 0
        audio_map = None
        bfwav_groups = None
        bars_cache: dict[str, Bars] = {}
        for i, action in enumerate(selected_actions, 1):
            kind = action.get("kind", "copy")
            src = action["src"]
            dst = action["dst"]
            if kind == "bars":
                if audio_map is None:
                    audio_map = _load_audio_map_at(Path(self.mod_root))
                    if audio_map is None:
                        lines.append("[Audio] audio_assets_map.json not found, patch canceled.")
                        continue
                if bfwav_groups is None:
                    bfwav_groups = _load_bfwav_groups_at(Path(self.mod_root))
                print(f"\n--- Processing #{patched_count + 1}: {dst} ---")
                res = self._execute_bars_action(action, audio_map, bfwav_groups, bars_cache)
                if res:
                    patched_count += 1
                continue
            os.makedirs(dst.parent, exist_ok=True)
            shutil.copy2(src, dst)
            copied_count += 1

        parts = []
        if copied_count:
            parts.append(f"{copied_count} file(s) copied")
        if patched_count:
            parts.append(f"{patched_count} .bars file(s) patched")
        if not parts:
            lines.append("No action executed.")
        else:
            lines.append("Copy finished: " + " | ".join(parts) + ".")
        return "\n".join(lines).rstrip()

    def _execute_bars_action(self, action: dict, audio_map, bfwav_groups, bars_cache) -> bool:
        src = Path(action["src"])
        dst = Path(action["dst"])
        base_audio_dir = Path(self.mod_root) / BASE_AUDIO_DIRNAME

        if not src.is_file():
            print(f"[ERROR] Source .bars not found: {src}")
            return False

        if not dst.exists():
            dst.parent.mkdir(parents=True, exist_ok=True)
            candidates = [
                base_audio_dir / "Driver" / dst.name,
                base_audio_dir / "DriverMenu" / dst.name,
            ]
            found = False
            for candidate in candidates:
                if candidate.is_file():
                    shutil.copy2(candidate, dst)
                    print(f"[INFO] Missing destination file, copied from {candidate}")
                    found = True
                    break
            if not found:
                print(f"[ERROR] Source file not found for {dst.name} in Audio/Driver*.")
                return False

        res = _process_bars_pair(str(src), str(dst), audio_map, bfwav_groups, bars_cache)
        if res is None:
            return False
        replaced, ignored = res
        if replaced == 0:
            print(f"[WARN] No replacement performed for {dst.name}.")
        return True

    def copy_files(self):
        if not self._ensure_mod_root():
            return

        def slot_fixed_name(parent_idx, gi):
            names = GROUP_SLOT_NAMES.get(parent_idx, [])
            return names[gi] if gi < len(names) else None

        # refresh mapping if missing
        if not self.mapping:
            self.mapping = self._build_default_mapping()
            self._save_mapping()

        assignments = []
        # collect placements: grid
        for idx in range(GRID_COUNT):
            if idx in BLOCKED_INDICES:
                continue
            fixed = CASE_NAME_BY_INDEX.get(idx)
            ch = self.grid[idx]
            if fixed and ch:
                assignments.append((fixed, ch["name"]))
        # groups
        def slot_fixed_name(parent_idx, gi):
            names = GROUP_SLOT_NAMES.get(parent_idx, [])
            return names[gi] if gi < len(names) else None
        for parent_idx, group in self.groups.items():
            for gi in range(group["size"]):
                fixed = slot_fixed_name(parent_idx, gi)
                ch = group["slots"][gi]
                if fixed and ch:
                    assignments.append((fixed, ch["name"]))

        dst_root = os.path.join(self.mod_root, "romfs")
        copied = 0
        missing = []

        # copy shared UI sarc
        ui_common_dst = os.path.join(dst_root, "UI", "cmn")
        os.makedirs(ui_common_dst, exist_ok=True)
        for shared in ("common.sarc", "menu.sarc"):
            src_shared = os.path.join(self.mod_root, shared)
            if os.path.isfile(src_shared):
                shutil.copy2(src_shared, os.path.join(ui_common_dst, shared))
                copied += 1
            else:
                missing.append(shared)

        for fixed_name, folder_name in assignments:
            base = os.path.join(self.mod_root, "characters", folder_name)
            files = self.mapping.get(fixed_name, self._default_files_for(fixed_name))
            driver_path = files.get("Driver") or files.get("driver")
            menu_path = files.get("Audio/DriverMenu") or files.get("menu")
            audio_path = files.get("Audio/Driver") or files.get("audio")
            rel_paths = [
                driver_path,
                menu_path,
                audio_path,
                os.path.join("UI", "cmn", files.get("ui")) if files.get("ui") else None,
                os.path.join("UI", "cmn", files.get("ui_ed")) if files.get("ui_ed") else None,
                os.path.join("UI", "cmn", files.get("ui_map")) if files.get("ui_map") else None,
            ]
            for rel in rel_paths:
                if not rel:
                    continue
                src = os.path.join(base, rel)
                if not os.path.isfile(src):
                    missing.append(f"{folder_name}/{rel}")
                    continue
                dst = os.path.join(dst_root, rel)
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                shutil.copy2(src, dst)
                copied += 1

        msg = f"Copied {copied} file(s) to romfs."
        if missing:
            short = ", ".join(missing[:6])
            if len(missing) > 6:
                short += "..."
            messagebox.showwarning("Missing files", f"{len(missing)} missing file(s): {short}")
            msg += f" Missing: {len(missing)}."
        else:
            messagebox.showinfo("Copy complete", msg)
        self.status.set(msg)


    # ---------------- Presets ----------------

    def export_preset(self):
        if not self.characters:
            messagebox.showerror("Error", "Load characters first.")
            return

        # Fill empties with unused if possible (global across grid + group slots)
        used = set()

        # collect positions
        grid_out = []
        for i in range(GRID_COUNT):
            if i in self.groups:
                grid_out.append(None)  # placeholder for group dict
            else:
                ch = self.grid[i]
                if ch is not None:
                    used.add(ch["path"])
                grid_out.append(ch)

        group_out = {}
        for parent_idx, group in self.groups.items():
            slots_copy = list(group["slots"])
            for ch in slots_copy:
                if ch is not None:
                    used.add(ch["path"])
            group_out[parent_idx] = slots_copy

        pool = [c for c in self.characters if c["path"] not in used]

        def fill_if_none(ch):
            nonlocal pool
            if ch is not None:
                return ch
            if pool:
                pick = random.choice(pool)
                pool.remove(pick)
                used.add(pick["path"])
                return pick
            return random.choice(self.characters)

        # fill normal grid
        for i in range(GRID_COUNT):
            if i in self.groups:
                continue
            grid_out[i] = fill_if_none(grid_out[i])

        # fill group slots
        for parent_idx, slots in group_out.items():
            for gi in range(len(slots)):
                slots[gi] = fill_if_none(slots[gi])

        data_grid = []
        for i in range(GRID_COUNT):
            if i in self.groups:
                slots = group_out[i]
                data_grid.append({
                    "type": "group",
                    "size": self.groups[i]["size"],
                    "slots": [ch["name"] if ch else None for ch in slots]
                })
            else:
                data_grid.append(grid_out[i]["name"] if grid_out[i] else None)

        data = {"grid_files": data_grid}

        path = filedialog.asksaveasfilename(defaultextension=".json", filetypes=[("Preset", "*.json")])
        if path:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)

    def import_preset(self):
        path = filedialog.askopenfilename(filetypes=[("Preset", "*.json")])
        if not path:
            return
        if not self.characters:
            messagebox.showerror("Error", "Load characters first (to resolve files).")
            return

        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)

        grid_files = data.get("grid_files") or data.get("grid")
        if not isinstance(grid_files, list) or len(grid_files) != GRID_COUNT:
            messagebox.showerror("Error", f"Invalid preset: expected {GRID_COUNT} entries.")
            return

        mapping_file = {c["file"]: c for c in self.characters}
        mapping_name = {c["name"]: c for c in self.characters}

        def resolve_char(key):
            if key is None:
                return None
            return mapping_name.get(key) or mapping_file.get(key)

        missing = 0

        for i, entry in enumerate(grid_files):
            if i in self.groups:
                expected = self.groups[i]["size"]
                # entry may be dict group, or old string
                if isinstance(entry, dict) and entry.get("type") == "group":
                    slots = entry.get("slots", [])
                    slots = (slots + [None] * expected)[:expected]
                    resolved = []
                    for fname in slots:
                        ch = resolve_char(fname)
                        if fname and ch is None:
                            missing += 1
                        resolved.append(ch)
                    self.groups[i]["slots"] = resolved
                else:
                    # old format: a filename in parent cell -> put it in slot0
                    fname = entry
                    ch = resolve_char(fname)
                    if fname and ch is None:
                        missing += 1
                    resolved = [None] * expected
                    if expected:
                        resolved[0] = ch
                    self.groups[i]["slots"] = resolved

                # render parent after
                continue

            # normal cells
            fname = entry
            ch = resolve_char(fname)
            if fname and ch is None:
                missing += 1
            self.grid[i] = ch

        for i in range(GRID_COUNT):
            self.render_cell(i)

        if self.group_overlay is not None:
            self._render_group_overlay()

        if missing:
            messagebox.showwarning("Partial import", f"{missing} preset file(s) are missing in the loaded folder.")


# ---------------- MAIN ----------------

if __name__ == "__main__":
    app = MK8DXEditor()
    app.minsize(1000, 650)
    app.mainloop()
