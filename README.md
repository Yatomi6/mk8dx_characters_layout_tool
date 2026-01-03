# MK8 Deluxe Character Layout Editor

Tkinter toolkit to edit the Character Select Screen layout and prepare Mario Kart 8 Deluxe mod files (skins, audio, UI).

### Requirements
- Python 3 with `pip install -r requirements.txt` (Pillow only).
- Repository root must contain `characters/`, `config/`, `Audio/`, `common.sarc`, `menu.sarc`, etc.

### Character Manager (main tool)
Run:
```sh
python scripts/mk8dx_character_manager.py
```
Then pick the `mods_characters_mk8dx/` root when prompted.

What it does:
- Loads all skins from `characters/` using `config/mapping.json` and shows their icons.
- Checks every skin folder for expected files (Driver, Audio/Driver, Audio/DriverMenu, UI/cmn). Missing files are listed; you can choose to get them. A large missing list is written to `missing_files.txt`.
- Drag & drop characters from the left list to the 8x6 grid; grouped slots handle Gold/Metal Mario, Animal Boy/Girl, and Link/Link BotW. Cells (0,5) and (7,5) are blocked.
- Buttons: clear grid, randomize grid, import/export presets (`.json`), and **Copy files**.
- **Copy files** copies all placed assets into `romfs/` (including updated `common.sarc` and `menu.sarc` into `romfs/UI/cmn/`).

### Presets
- Export preset: fills empty cells with unused characters, then saves a `.json` layout.
- Import preset: loads a `.json` layout created by this tool or the legacy format.
- Store your own presets under `presets/` if you want.

### Custom skins
1. Create `characters/<SkinName>/` with `Driver/`, `Audio/Driver`, `Audio/DriverMenu`, and `UI/cmn/` inside.
2. Bones are now injected automatically: when the tool duplicates missing files or when you press **Copy files**, it loads `MK8D_Bones/` and adds every `.bfbon` into the matching model inside each `.szs`. (You no longer need to import bones manually in Switch Toolbox.)
3. Make sure file names match `config/mapping.json`.
4. Relaunch the tool; the icon shows up and can be placed.

### Mod installation
After copying files, place `<mod-name>/romfs/...` in your MK8DX mod folder.

### Extra scripts
- `scripts/generate_audio_assets_map.py`: rebuilds `config/audio_assets_map.json` by scanning the `Audio/` directory. Run `python scripts/generate_audio_assets_map.py`.
- `scripts/replace_bfwav_with_groups.py`: CLI to clone BFWAVs between `.bars` using `config/bfwav_groups.json` (needs pythonnet + DLLs in `scripts/lib`). Run `python scripts/replace_bfwav_with_groups.py --src ... --dst ... --output ...`.
- `scripts/add_bfbon_bones.py`: helper to inject `.bfbon` bones from `MK8D_Bones/` into a `.szs` model (needs oead + BFRES DLLs in `scripts/lib`).
- `scripts/replace_bftex_texture.py`: change icons' `.bftex` textures of `common.sarc` and `menu.sarc` with textures of the `.pngs` of the chosen folder.

### Repository layout
- `scripts/`: main UI (`mk8dx_character_manager.py`) and audio helpers.
- `config/`: `mapping.json` (slot -> files), `audio_assets_map.json` (bars prefixes/BFWAV lists), `bfwav_groups.json` (equivalent sounds for bfwav replacements), `character_files_reference.json` (expected file names). DO NOT TOUCH THIS FOLDER IF NOT NEEDED.
- `characters/`: base skins and their assets.
- `Audio/`: source `.bars` used for duplication/patching (DO NOT TOUCH).
- `romfs/`: output folder produced by **Copy files**.
- `MK8D_Bones/`: reference `*.bfbon` bone files (DO NOT TOUCH).
- `common.sarc`, `menu.sarc`: shared UI archives copied into `romfs/UI/cmn/`.