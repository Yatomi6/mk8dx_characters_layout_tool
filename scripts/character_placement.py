import os
import json
import math
import random
import shutil
from pathlib import Path
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from PIL import Image, ImageTk

ICON = 64
TEXT_H = 28

GRID_COLS = 8
GRID_ROWS = 6
GRID_COUNT = GRID_COLS * GRID_ROWS

CONFIG_DIRNAME = "config"

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
    43: "Link",
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
        "Link",
        "LinkBotw",
    ],
}

PREFIX = "tc_Chara_"
IMAGE_EXT = (".png", ".jpg", ".jpeg", ".bmp", ".gif", ".webp")

def _config_path(base: Path, filename: str) -> Path:
    config_dir = base / CONFIG_DIRNAME
    candidates = [config_dir / filename, base / filename]
    for cand in candidates:
        if cand.exists():
            return cand
    return candidates[0]


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
        self.mod_root = None  # mods_characters_mk8dx folder
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

        ttk.Button(toolbar, text="Charger personnages", command=self.load_characters).pack(side="left")
        ttk.Button(toolbar, text="Vider grille", command=self.clear_grid).pack(side="left", padx=6)
        ttk.Button(toolbar, text="Randomiser grille", command=self.randomize_grid).pack(side="left", padx=6)
        ttk.Button(toolbar, text="Importer preset", command=self.import_preset).pack(side="left", padx=18)
        ttk.Button(toolbar, text="Exporter preset", command=self.export_preset).pack(side="left")
        ttk.Button(toolbar, text="Copier fichiers", command=self.copy_files).pack(side="left", padx=12)

        self.status = tk.StringVar(value="Sélectionne mods_characters_mk8dx pour charger les vignettes.")
        ttk.Label(toolbar, textvariable=self.status).pack(side="left", padx=18)

        main = ttk.Frame(self, padding=6)
        main.pack(fill="both", expand=True)

        # Library
        lib_frame = ttk.Frame(main)
        lib_frame.pack(side="left", fill="y")

        ttk.Label(lib_frame, text="Personnages").pack(anchor="w")

        self.lib = ScrollableFrame(lib_frame, width=320, height=560)
        self.lib.pack(fill="y", pady=(6, 0))

        # Grid
        grid_frame = ttk.Frame(main, padding=(12, 0))
        grid_frame.pack(side="left")

        ttk.Label(grid_frame, text="Grille 8×6").pack(anchor="w")

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

    def _ensure_mod_root(self):
        if self.mod_root and os.path.isdir(self.mod_root):
            return True
        path = filedialog.askdirectory(title="Dossier mods_characters_mk8dx")
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

        chars_root = os.path.join(self.mod_root, "characters")
        subdirs = [d for d in os.listdir(chars_root) if os.path.isdir(os.path.join(chars_root, d))]

        for name in sorted(subdirs):
            cmn_dir = os.path.join(chars_root, name, "UI", "cmn")
            icon_path = None
            if os.path.isdir(cmn_dir):
                for f in sorted(os.listdir(cmn_dir)):
                    if f.lower().startswith("tc_chara") and f.lower().endswith(IMAGE_EXT):
                        icon_path = os.path.join(cmn_dir, f)
                        break
            tkimg = None
            if icon_path and os.path.isfile(icon_path):
                try:
                    img = Image.open(icon_path).convert("RGBA")
                    img = img.resize((ICON, ICON), Image.Resampling.LANCZOS)
                    tkimg = ImageTk.PhotoImage(img)
                except Exception:
                    missing_icons.append(name)
            else:
                missing_icons.append(name)

            char = {"path": icon_path or "", "file": name, "name": name, "image": tkimg}
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

        msg = f"{len(self.characters)} icônes chargées depuis {chars_root}"
        if missing_icons:
            msg += f" (icônes absentes/corrompues pour: {', '.join(sorted(set(missing_icons))[:6])}"
            if len(missing_icons) > 6:
                msg += "..."
            msg += ")"
        self.status.set(msg)

        dup_report = self._dup_process_base_folder(Path(chars_root))
        print(dup_report)
        self.status.set(f"{msg} | Duplication dossiers: ok")

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
                # On est sur le ghost : essayer de récupérer la vraie cible derrière.
                # Astuce simple : décaler un tout petit peu le point.
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
            messagebox.showerror("Erreur", "Charge d’abord des personnages.")
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
            "driver": f"{name}.szs",
            "menu": f"MenuDriver_{name}.bars",
            "audio": f"Driver_{name}.bars",
            "ui": f"tc_Chara_{name}^l.png",
            "ui_ed": f"tc_edChara_{name}^l.png",
            "ui_map": f"tc_MapChara_{name}^l.png",
        }

    def _build_default_mapping(self):
        mapping = {}
        for name in sorted(self._all_fixed_names()):
            mapping[name] = self._default_files_for(name)
        return mapping

    def _normalize_mapping_entry(self, key, val):
        base = self._default_files_for(key)
        if isinstance(val, dict):
            merged = base.copy()
            for k, v in val.items():
                if k in base and v:
                    merged[k] = v
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

    def _dup_process_single(self, child_dirs: list[Path], relative: str, lines: list[str]):
        union_names: set[str] = set()
        child_files: dict[str, list[str]] = {}

        for child in child_dirs:
            folder = child / relative
            if not folder.is_dir():
                lines.append(f"[{relative}] {child.name}: sous-dossier manquant, ignore")
                continue
            files = self._dup_list_files(folder)
            if not files:
                lines.append(f"[{relative}] {child.name}: aucun fichier, ignore")
                continue
            union_names.update(files)
            child_files[child.name] = files

        lines.append(f"[{relative}] Noms repertories: {len(union_names)}")

        for child in child_dirs:
            folder = child / relative
            files = child_files.get(child.name, [])
            if not files:
                continue
            if 2 <= len(files) <= 10:
                joined = ", ".join(files)
                lines.append(f"WARNING [{relative}] {child.name}: plusieurs fichiers ({joined}), ignore ce dossier")
                continue
            template_name = files[0]
            template_path = folder / template_name
            copies = 0
            for name in sorted(union_names):
                dest = folder / name
                if dest.exists():
                    continue
                shutil.copy2(template_path, dest)
                copies += 1
            if copies > 0:
                lines.append(f"[{relative}] {child.name}: copie {copies} fichier(s) depuis {template_name}")

    def _dup_process_ui_cmn(self, child_dirs: list[Path], lines: list[str]):
        relative = Path("UI/cmn")
        prefixes = ["tc_edChara", "tc_Chara", "tc_MapChara"]
        union_by_prefix: dict[str, set[str]] = {p: set() for p in prefixes}
        child_prefix_files: dict[str, dict[str, list[str]]] = {}

        for child in child_dirs:
            folder = child / relative
            if not folder.is_dir():
                lines.append(f"[{relative}] {child.name}: sous-dossier manquant, ignore")
                continue
            files = self._dup_list_files(folder)
            child_prefix_files[child.name] = {}
            for prefix in prefixes:
                matched = [f for f in files if f.startswith(prefix)]
                child_prefix_files[child.name][prefix] = matched
                union_by_prefix[prefix].update(matched)

        for prefix, names in union_by_prefix.items():
            lines.append(f"[{relative}] {prefix}: Noms repertories: {len(names)}")

        for child in child_dirs:
            folder = child / relative
            if not folder.is_dir():
                continue
            prefix_map = child_prefix_files.get(child.name, {})
            for prefix in prefixes:
                matches = prefix_map.get(prefix, [])
                if not matches:
                    continue
                if 2 <= len(matches) <= 10:
                    joined = ", ".join(matches)
                    lines.append(f"WARNING [{relative}] {child.name}: plusieurs fichiers pour {prefix} ({joined}), ignore ce prefixe")
                    continue
                template_name = matches[0]
                template_path = folder / template_name
                copies = 0
                for name in sorted(union_by_prefix[prefix]):
                    dest = folder / name
                    if dest.exists():
                        continue
                    shutil.copy2(template_path, dest)
                    copies += 1
                if copies > 0:
                    lines.append(f"[{relative}] {child.name} {prefix}: copie {copies} fichier(s) depuis {template_name}")

    def _dup_process_base_folder(self, base_folder: Path) -> str:
        if not base_folder.exists():
            return f"Chemin introuvable: {base_folder}"
        child_dirs = sorted([p for p in base_folder.iterdir() if p.is_dir()], key=lambda p: p.name.lower())
        if not child_dirs:
            return "Aucun sous-dossier trouve."
        lines: list[str] = [f"Dossier parent: {base_folder}"]
        for relative in ["Audio/Driver", "Audio/DriverMenu", "Driver"]:
            self._dup_process_single(child_dirs, relative, lines)
            lines.append("")
        self._dup_process_ui_cmn(child_dirs, lines)
        return "\n".join(lines).rstrip()

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

        for fixed_name, folder_name in assignments:
            base = os.path.join(self.mod_root, "characters", folder_name)
            files = self.mapping.get(fixed_name, self._default_files_for(fixed_name))
            rel_paths = [
                ("Driver", files.get("driver")),
                (os.path.join("Audio", "DriverMenu"), files.get("menu")),
                (os.path.join("Audio", "Driver"), files.get("audio")),
                (os.path.join("UI", "cmn"), files.get("ui")),
                (os.path.join("UI", "cmn"), files.get("ui_ed")),
                (os.path.join("UI", "cmn"), files.get("ui_map")),
            ]
            for rel_dir, fname in rel_paths:
                rel = os.path.join(rel_dir, fname)
                src = os.path.join(base, rel)
                if not os.path.isfile(src):
                    missing.append(f"{folder_name}/{rel}")
                    continue
                dst = os.path.join(dst_root, rel)
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                shutil.copy2(src, dst)
                copied += 1

        msg = f"Copié {copied} fichier(s) vers romfs."
        if missing:
            short = ", ".join(missing[:6])
            if len(missing) > 6:
                short += "..."
            messagebox.showwarning("Fichiers manquants", f"{len(missing)} fichier(s) manquants: {short}")
            msg += f" Manquants: {len(missing)}."
        else:
            messagebox.showinfo("Copie terminée", msg)
        self.status.set(msg)


    # ---------------- Presets ----------------

    def export_preset(self):
        if not self.characters:
            messagebox.showerror("Erreur", "Charge d’abord des personnages.")
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
            messagebox.showerror("Erreur", "Charge d’abord des personnages (pour résoudre les fichiers).")
            return

        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)

        grid_files = data.get("grid_files") or data.get("grid")
        if not isinstance(grid_files, list) or len(grid_files) != GRID_COUNT:
            messagebox.showerror("Erreur", f"Preset invalide: il faut {GRID_COUNT} entrées.")
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
            messagebox.showwarning("Import partiel", f"{missing} fichiers du preset manquent dans le dossier chargé.")


# ---------------- MAIN ----------------

if __name__ == "__main__":
    app = MK8DXEditor()
    app.minsize(1000, 650)
    app.mainloop()
