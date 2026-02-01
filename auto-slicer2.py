#!/usr/bin/env python

import os
import time
import subprocess
import shutil
import configparser
from pathlib import Path

# --- Initialization ---
config = configparser.ConfigParser()
config.read('config.ini')

WATCH_DIR = Path(config['PATHS']['search_directory'])
ARCHIVE_DIR = Path(config['PATHS']['archive_directory'])
CURA_BIN = Path(config['PATHS']['cura_engine_path'])
DEF_DIR = Path(config['PATHS']['definition_dir'])
PRINTER_DEF = config['PATHS']['printer_definition']
DEFAULTS = dict(config['DEFAULT_SETTINGS'])

# Ensure environment is set up for the bundled libraries
# This mimics your successful 'LD_LIBRARY_PATH=~/squashfs-root' test
ENV = os.environ.copy()
SQUASH_ROOT = str(CURA_BIN.parent.absolute())
ENV["LD_LIBRARY_PATH"] = f"{SQUASH_ROOT}:{ENV.get('LD_LIBRARY_PATH', '')}"

# Create directories if they don't exist
ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
WATCH_DIR.mkdir(parents=True, exist_ok=True)

def get_overrides(file_path):
    """Parses parent folder names for 'key-value' pairs."""
    overrides = {}
    for parent in file_path.parents:
        if parent == WATCH_DIR:
            break
        if "-" in parent.name:
            try:
                key, val = parent.name.split("-", 1)
                overrides[key] = val
            except ValueError:
                continue
    return overrides

def slice_file(stl_path):
    print(f"\n[Slicing] {stl_path.name}")
    
    # Merge defaults and folder-based overrides
    active_settings = DEFAULTS.copy()
    overrides = get_overrides(stl_path)
    active_settings.update(overrides)
    
    gcode_path = stl_path.with_suffix('.gcode')

    # Construct the CuraEngine CLI command
    # -d: path to definition search directory
    # -j: the printer definition file
    cmd = [
        str(CURA_BIN), "slice",
        "-d", str(DEF_DIR),
        "-j", PRINTER_DEF,
        "-l", str(stl_path),
        "-o", str(gcode_path)
    ]
    
    # Append all settings as -s key=value
    for key, val in active_settings.items():
        cmd.extend(["-s", f"{key}={val}"])

    try:
        # Execute from DEF_DIR to help CuraEngine resolve relative paths
        result = subprocess.run(cmd, env=ENV, cwd=str(DEF_DIR), capture_output=True, text=True)
        
        if result.returncode == 0:
            print(f"  Success: Generated {gcode_path.name}")
            
            # Archive logic: move to a timestamped subfolder to avoid name collisions
            timestamp = time.strftime("%Y%m%d_%H%M%S")
            job_folder = ARCHIVE_DIR / f"{stl_path.stem}_{timestamp}"
            job_folder.mkdir(exist_ok=True)
            
            shutil.move(str(stl_path), job_folder / stl_path.name)
            if gcode_path.exists():
                shutil.move(str(gcode_path), job_folder / gcode_path.name)
        else:
            print(f"  Error in CuraEngine: {result.stderr}")
            # Optional: move failed files to an 'errors' folder
            error_dir = ARCHIVE_DIR / "errors"
            error_dir.mkdir(exist_ok=True)
            shutil.move(str(stl_path), error_dir / stl_path.name)
            
    except Exception as e:
        print(f"  System error processing {stl_path.name}: {e}")

def main():
    print(f"Watcher active on {WATCH_DIR}...")
    print(f"Using library path: {SQUASH_ROOT}")
    
    while True:
        # Look for .stl files recursively
        files = list(WATCH_DIR.rglob('*.stl'))
        
        for file in files:
            # Wait for file transfer to complete (size stability check)
            try:
                size_pre = file.stat().st_size
                time.sleep(1)
                if size_pre == file.stat().st_size:
                    slice_file(file)
            except FileNotFoundError:
                continue # File might have been moved/deleted during check
        
        time.sleep(5)

if __name__ == "__main__":
    main()
