import json
import sys
import shutil
import random
from io import BytesIO
from pathlib import Path
import struct
import tkinter as tk
from tkinter import filedialog as fd, messagebox, ttk

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from mk8dx_audio_classes import Bars, calculate_crc32_hash, pad_till


CONFIG_DIR = REPO_ROOT / "config"


def _find_config_file(name: str) -> Path:
    candidates = [CONFIG_DIR / name, REPO_ROOT / name]
    for cand in candidates:
        if cand.exists():
            return cand
    return candidates[0]


AUDIO_MAP_PATH = _find_config_file("audio_assets_map.json")
BFWAV_GROUPS_PATH = _find_config_file("bfwav_groups.json")
BASE_AUDIO_DIR = REPO_ROOT / "Audio"


def load_audio_map():
    if not AUDIO_MAP_PATH.exists():
        print(f"[ERREUR] audio_assets_map.json introuvable à la racine ({AUDIO_MAP_PATH})")
        sys.exit(1)
    with AUDIO_MAP_PATH.open("r", encoding="utf-8") as f:
        return json.load(f)


def load_bfwav_groups():
    if not BFWAV_GROUPS_PATH.exists():
        return {}
    try:
        data = json.loads(BFWAV_GROUPS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}
    name_to_group = {}
    for grp in data.get("groups", []):
        members = set(grp)
        for name in members:
            name_to_group[name] = members
    return name_to_group


def find_map_entry(audio_map, bars_path: str):
    """Retourne la ligne de config correspondant au fichier .bars choisi."""
    name = Path(bars_path).name
    for section, entries in audio_map.items():
        if name in entries:
            entry = entries[name].copy()
            entry["section"] = section
            return entry
    return None


def pick_source_file():
    root = tk.Tk()
    root.withdraw()
    source_path = fd.askopenfilename(title="Sélectionner le BARS SOURCE...", filetypes=[("BARS Files", "*.bars")])
    root.destroy()
    if not source_path:
        sys.exit()
    return source_path


def select_destinations(expected_names, dest_dir: Path):
    """Affiche une liste cochable des .bars attendus (existant ou non) et renvoie les chemins sélectionnés."""
    win = tk.Tk()
    win.title("Choisir les BARS destination")
    win.attributes("-topmost", True)
    win.lift()
    win.focus_force()
    # Centrer la fenêtre sur l'écran
    win.update_idletasks()
    width, height = 620, 560
    sx = (win.winfo_screenwidth() - width) // 2
    sy = (win.winfo_screenheight() - height) // 2
    win.geometry(f"{width}x{height}+{sx}+{sy}")

    ttk.Label(win, text=f"Dossier: {dest_dir}", padding=(10, 6)).pack(anchor="w")

    canvas = tk.Canvas(win, height=360)
    vsb = ttk.Scrollbar(win, orient="vertical", command=canvas.yview)
    canvas.configure(yscrollcommand=vsb.set)
    canvas.pack(side="left", fill="both", expand=True)
    vsb.pack(side="right", fill="y")

    inner = ttk.Frame(canvas)
    inner_id = canvas.create_window((0, 0), window=inner, anchor="nw")

    entries = []
    for name in expected_names:
        target = dest_dir / name
        exists = target.exists()
        label = f"{name} {'(existe)' if exists else '(manquant)'}"
        var = tk.BooleanVar(value=True)
        chk = ttk.Checkbutton(inner, text=label, variable=var)
        chk.pack(anchor="w", pady=1, padx=6)
        entries.append((var, target))

    def _on_configure(_):
        canvas.configure(scrollregion=canvas.bbox("all"))
    inner.bind("<Configure>", _on_configure)

    def _on_canvas_configure(event):
        canvas.itemconfigure(inner_id, width=event.width)
    canvas.bind("<Configure>", _on_canvas_configure)

    btn_frame = ttk.Frame(win, padding=(10, 6))
    btn_frame.pack(fill="x")

    def select_all(val: bool):
        for var, _ in entries:
            var.set(val)

    ttk.Button(btn_frame, text="Tout cocher", command=lambda: select_all(True)).pack(side="left", padx=(0, 6))
    ttk.Button(btn_frame, text="Tout décocher", command=lambda: select_all(False)).pack(side="left")

    result = {"paths": None}

    def on_ok():
        selected = [path for var, path in entries if var.get()]
        result["paths"] = selected
        win.destroy()

    def on_cancel():
        result["paths"] = []
        win.destroy()

    ttk.Button(btn_frame, text="Copier", command=on_ok).pack(side="right", padx=(6, 0))
    ttk.Button(btn_frame, text="Annuler", command=on_cancel).pack(side="right")

    win.protocol("WM_DELETE_WINDOW", on_cancel)
    win.mainloop()
    return result["paths"] or []


def swap_prefix(name: str, src_prefix: str, dest_prefix: str) -> str:
    if src_prefix and dest_prefix and src_prefix in name:
        return name.replace(src_prefix, dest_prefix, 1)
    return name


def asset_to_bytes(asset) -> bytes:
    buf = BytesIO()
    asset.write(buf)
    return buf.getvalue()


def write_header_updates(dest_bytes: bytearray, bars_dest: Bars):
    """Réécrit la taille et les asset_offsets dans l'en-tête sans toucher aux metas."""
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


def transfer_bfwavs(bars_source: Bars, bars_dest: Bars, src_info, dest_info, name_to_group: dict[str, set], dest_bytes: bytearray):
    src_prefix = src_info.get("prefix", "")
    dest_prefix = dest_info.get("prefix", "")
    src_list = src_info.get("bfwav", []) or []
    dest_list = set(dest_info.get("bfwav", []) or [])

    src_crc_to_idx = {crc: idx for idx, crc in enumerate(bars_source.crc_hashes)}
    dest_crc_to_idx = {crc: idx for idx, crc in enumerate(bars_dest.crc_hashes)}

    # Build target -> candidate sources mapping
    target_to_sources = {}
    for src_name in src_list:
        # Priorité : mapping direct par préfixe (source -> dest)
        direct_target = swap_prefix(src_name, src_prefix, dest_prefix)
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
        # Choisir de préférence une source dont le swap préfixe == dest_name
        def swapped_ok(n):
            return swap_prefix(n, src_prefix, dest_prefix) == dest_name
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

        new_data = asset_to_bytes(bars_source.assets[src_idx_resolved])
        new_size = pad_till(len(new_data))
        padded = new_data + b"\x00" * (new_size - len(new_data))

        size_diff = new_size - slot_size

        if size_diff <= 0:
            # Remplacement en place, garder la taille du slot pour ne pas toucher aux offsets
            dest_bytes[start:end] = padded + b"\x00" * (-size_diff)
        else:
            # Besoin d'agrandir : on insère et on décale les offsets suivants
            dest_bytes[start:end] = padded
            for i, off in enumerate(bars_dest.asset_offsets):
                if off > start:
                    bars_dest.asset_offsets[i] = off + size_diff
            bars_dest.size = len(dest_bytes)

        replaced += 1

    return replaced, ignored, dest_bytes


def run_transfer_tool():
    audio_map = load_audio_map()
    bfwav_groups = load_bfwav_groups()
    source_path = pick_source_file()

    src_name = Path(source_path).name
    src_entry = find_map_entry(audio_map, source_path)
    if not src_entry:
        print(f"[ERREUR] {src_name} introuvable dans audio_assets_map.json, arrêt.")
        sys.exit(1)

    section = src_entry.get("section")
    if not section or section not in audio_map:
        print(f"[ERREUR] Section manquante pour {src_name} dans audio_assets_map.json.")
        sys.exit(1)

    expected_names = sorted(n for n in audio_map[section].keys() if n != src_name)
    dest_dir = Path(source_path).parent
    dest_paths = select_destinations(expected_names, dest_dir)
    if not dest_paths:
        print("[INFO] Aucun fichier sélectionné, arrêt.")
        sys.exit()

    try:
        bars_source_cached = Bars(source_path)
    except Exception as e:
        print(f"[ERREUR] Lecture source échouée ({source_path}): {e}")
        sys.exit(1)

    total_replaced = 0
    total_ignored = 0
    for i, dest_path in enumerate(dest_paths, 1):
        dest_path = Path(dest_path)
        if dest_path.resolve() == Path(source_path).resolve():
            print(f"[INFO] Fichier source ignoré dans la sélection: {dest_path}")
            continue
        if not dest_path.exists():
            dest_path.parent.mkdir(parents=True, exist_ok=True)
            candidates = [
                BASE_AUDIO_DIR / "Driver" / dest_path.name,
                BASE_AUDIO_DIR / "DriverMenu" / dest_path.name,
            ]
            found = False
            for candidate in candidates:
                if candidate.is_file():
                    shutil.copy2(candidate, dest_path)
                    print(f"[INFO] Fichier destination manquant, copie depuis {candidate}")
                    found = True
                    break
            if not found:
                print(f"[ERREUR] Fichier source introuvable pour {dest_path.name} dans Audio/Driver*.")
                sys.exit(1)
        print(f"\n--- Traitement #{i} : {dest_path} ---")
        res = process_pair(
            str(source_path),
            str(dest_path),
            audio_map,
            overwrite=True,
            bars_source_cached=bars_source_cached,
            bfwav_groups=bfwav_groups,
        )
        if res:
            r, ign = res
            total_replaced += r
            total_ignored += len(ign)

    print(f"\n[FIN] Remplacements totaux : {total_replaced} | Ignorés : {total_ignored}")


def process_pair(source_path, dest_path, audio_map, overwrite: bool = False, bars_source_cached=None, bfwav_groups=None):
    src_info = find_map_entry(audio_map, source_path)
    dest_info = find_map_entry(audio_map, dest_path)

    if not src_info or not dest_info:
        missing = source_path if not src_info else dest_path
        print(f"[ERREUR] Impossible de trouver {Path(missing).name} dans audio_assets_map.json")
        return None

    bars_source = bars_source_cached
    if bars_source is None:
        print(f"Chargement source : {source_path}")
        try:
            bars_source = Bars(source_path)
        except Exception as e:
            print(f"[ERREUR] Lecture source échouée ({source_path}): {e}")
            return None

    try:
        bars_dest = Bars(dest_path)
    except Exception as e:
        print(f"[ERREUR] Lecture destination échouée ({dest_path}): {e}")
        return None
    dest_bytes = bytearray(Path(dest_path).read_bytes())

    bfwav_groups = bfwav_groups or {}
    replaced, ignored, dest_bytes = transfer_bfwavs(
        bars_source, bars_dest, src_info, dest_info, bfwav_groups, dest_bytes
    )
    write_header_updates(dest_bytes, bars_dest)
    print(f"Remplacements effectués : {replaced}")
    if ignored:
        print(f"Ignorés (absents dans la destination après remplacement de préfixe) : {len(ignored)}")

    if overwrite:
        Path(dest_path).write_bytes(dest_bytes)
    else:
        out_path = fd.asksaveasfilename(
            title="Enregistrer le nouveau BARS modifié...",
            initialfile=Path(dest_path).name,
            filetypes=[("BARS File", "*.bars")]
        )
        if out_path:
            Path(out_path).write_bytes(dest_bytes)
        else:
            print("[INFO] Sauvegarde annulée.")

    return replaced, ignored


if __name__ == "__main__":
    run_transfer_tool()
