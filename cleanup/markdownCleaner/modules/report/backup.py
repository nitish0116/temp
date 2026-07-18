"""
modules/report/backup.py

Automatic backup creation before processing.
"""

from __future__ import annotations

import shutil

import hashlib

import json

from pathlib import Path

from datetime import datetime


class BackupManager:
    """
    Handles original file backups.
    """

    def __init__(
        self,
        backup_root,
    ):

        self.backup_root = Path(backup_root)

    # ---------------------------------------------------------

    def create_backup(
        self,
        source_file,
    ):
        """
        Create timestamped backup.

        Returns:
            backup directory
        """

        source = Path(source_file)

        if not source.exists():

            raise FileNotFoundError(source)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

        backup_dir = self.backup_root / timestamp

        backup_dir.mkdir(
            parents=True,
            exist_ok=False,
        )

        destination = backup_dir / source.name

        shutil.copy2(
            source,
            destination,
        )

        metadata = {
            "original_file": str(source),
            "backup_file": str(destination),
            "created": datetime.now().isoformat(),
            "sha256": self._hash_file(source),
            "size": source.stat().st_size,
        }

        self._write_metadata(
            backup_dir,
            metadata,
        )

        return backup_dir

    # ---------------------------------------------------------

    def _hash_file(
        self,
        path,
    ):
        """
        Generate SHA256 checksum.
        """

        sha = hashlib.sha256()

        with open(
            path,
            "rb",
        ) as file:

            while chunk := file.read(1024 * 1024):

                sha.update(chunk)

        return sha.hexdigest()

    # ---------------------------------------------------------

    def _write_metadata(
        self,
        folder,
        metadata,
    ):

        file = Path(folder) / "metadata.json"

        with file.open(
            "w",
            encoding="utf-8",
        ) as output:

            json.dump(
                metadata,
                output,
                indent=2,
            )
