"""
Manifest of path -> content_hash, enabling incremental scans.

maintains a small JSON file that remembers each scanned file’s content hash.

Its main purpose is to support incremental scanning:
First scan:
scan every file

Later scans:
scan files again
compare their hashes with the manifest
process only added or changed files
detect removed files

The manifest is stored by default at:
.ibwd/manifest.json

Its contents might look like:
{
  "src/ibwd/graph/database.py": "a1b2c3...",
  "src/ibwd/scanner/filesystem.py": "d4e5f6..."
}
Each key is a file path, and each value is that file’s content hash.
"""

from __future__ import annotations

import json
from pathlib import Path
from ibwd.local_io import atomic_write

DEFAULT_MANIFEST_PATH = Path(".ibwd") / "manifest.json"


def load_manifest(manifest_path: Path = DEFAULT_MANIFEST_PATH) -> dict[str, str]:
    """
    Load the manifest from the given path, returning a dictionary of path -> content_hash.
    
    This function reads the previous manifest from disk.

    manifest_path is the location of the JSON file.
    It defaults to manifest.json.
    dict[str, str] means the function returns a dictionary mapping file paths to hashes.
    """
    if not manifest_path.exists(): # If the manifest does not exist, the function returns an empty dictionary. This normally happens during the first scan.
        return {} # Return an empty dictionary to indicate that there are no previously scanned files to compare against
    return json.loads(manifest_path.read_text()) # If the file exists: 1. Reads it as text. 2. Parses the JSON. 3. Returns the resulting dictionary.


def save_manifest(manifest: dict[str, str], manifest_path: Path = DEFAULT_MANIFEST_PATH) -> None: 
    """
    Save the manifest to the given path, creating parent directories if needed.
    
    This function saves the latest file-to-hash mapping.
    """
    manifest_path.parent.mkdir(parents=True, exist_ok=True) # Ensure the parent directory of the manifest file exists, creating it if necessary
    atomic_write(manifest_path, json.dumps(manifest, indent=2, sort_keys=True))
                                                                                # indent=2 makes the file readable.
                                                                                # sort_keys=True keeps entries in a consistent alphabetical order.
                                                                                # Consistent formatting makes changes easier to inspect and compare.


"""
How scan.py uses it
In scan.py:
previous_manifest = load_manifest(manifest_path)

The previous scan’s hashes are loaded.
Then the scanner compares each current file:
if previous_manifest.get(scanned_file.path) == scanned_file.content_hash:
    continue

If the old and current hashes match, the file has not changed, so symbol extraction is skipped.
At the end:
new_manifest = {f.path: f.content_hash for f in scanned}
save_manifest(new_manifest, manifest_path)   

A new manifest is built from the current scan and saved for the next run.
In short, manifest.py acts as the scanner’s memory of the previous scan, allowing the project to avoid expensive work on unchanged file
"""
