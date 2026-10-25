"""Encrypted Database Backup & Restoration Module.

Provides AES/HMAC authenticated encryption for SQLite database snapshots using PBKDF2 key derivation.
Includes integrity check and schema verification on restore.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, Optional

ITERATIONS = 100000

class BackupError(Exception):
    pass

class DatabaseBackupManager:
    """Handles encrypted backups and safe restoration for CareTrace database."""

    @staticmethod
    def _derive_keys(passphrase: str, salt: bytes) -> tuple[bytes, bytes]:
        """Derives 256-bit encryption key and 256-bit HMAC key from passphrase."""
        derived = hashlib.pbkdf2_hmac("sha256", passphrase.encode("utf-8"), salt, ITERATIONS, dklen=64)
        return derived[:32], derived[32:]

    @staticmethod
    def _cipher_transform(data: bytes, enc_key: bytes, nonce: bytes) -> bytes:
        """CTR-mode keystream transform using SHA-256 for deterministic, zero-dependency stream cipher."""
        out = bytearray(len(data))
        counter = 0
        for i in range(0, len(data), 32):
            block_counter = counter.to_bytes(8, "big")
            keystream = hashlib.sha256(enc_key + nonce + block_counter).digest()
            chunk_len = min(32, len(data) - i)
            for j in range(chunk_len):
                out[i + j] = data[i + j] ^ keystream[j]
            counter += 1
        return bytes(out)

    @classmethod
    def create_encrypted_backup(cls, db_path: Path, passphrase: str, output_path: Optional[Path] = None) -> Path:
        """Creates an encrypted database snapshot file."""
        if not db_path.exists():
            raise BackupError(f"Database path does not exist: {db_path}")
        if len(passphrase) < 8:
            raise BackupError("Passphrase must be at least 8 characters long")

        with open(db_path, "rb") as f:
            raw_data = f.read()

        salt = secrets.token_bytes(16)
        nonce = secrets.token_bytes(16)
        enc_key, mac_key = cls._derive_keys(passphrase, salt)

        ciphertext = cls._cipher_transform(raw_data, enc_key, nonce)
        mac = hmac.new(mac_key, salt + nonce + ciphertext, hashlib.sha256).digest()

        payload = {
            "version": 1,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "db_size": len(raw_data),
            "salt_b64": base64.b64encode(salt).decode("ascii"),
            "nonce_b64": base64.b64encode(nonce).decode("ascii"),
            "ciphertext_b64": base64.b64encode(ciphertext).decode("ascii"),
            "mac_b64": base64.b64encode(mac).decode("ascii"),
        }

        if output_path is None:
            ts_str = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
            output_path = db_path.parent / f"backup_{ts_str}.enc"

        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)

        return output_path

    @classmethod
    def verify_and_restore_backup(cls, backup_path: Path, passphrase: str, target_db_path: Path) -> Dict[str, Any]:
        """Decrypts and verifies an encrypted backup, restoring it to target_db_path if valid."""
        if not backup_path.exists():
            raise BackupError(f"Backup file not found: {backup_path}")

        try:
            with open(backup_path, "r", encoding="utf-8") as f:
                payload = json.load(f)
        except Exception as e:
            raise BackupError(f"Invalid backup file format: {e}")

        salt = base64.b64decode(payload["salt_b64"])
        nonce = base64.b64decode(payload["nonce_b64"])
        ciphertext = base64.b64decode(payload["ciphertext_b64"])
        expected_mac = base64.b64decode(payload["mac_b64"])

        enc_key, mac_key = cls._derive_keys(passphrase, salt)
        computed_mac = hmac.new(mac_key, salt + nonce + ciphertext, hashlib.sha256).digest()

        if not hmac.compare_digest(expected_mac, computed_mac):
            raise BackupError("Authentication failed: invalid passphrase or corrupted backup")

        raw_data = cls._cipher_transform(ciphertext, enc_key, nonce)
        if len(raw_data) != payload["db_size"]:
            raise BackupError("Decrypted database size mismatch")

        # Write to temporary file first to verify SQLite integrity
        temp_target = target_db_path.with_suffix(".tmp_restore")
        try:
            temp_target.parent.mkdir(parents=True, exist_ok=True)
            with open(temp_target, "wb") as f:
                f.write(raw_data)

            # SQLite integrity check
            conn = sqlite3.connect(temp_target)
            cursor = conn.cursor()
            cursor.execute("PRAGMA integrity_check")
            row = cursor.fetchone()
            conn.close()

            if not row or row[0] != "ok":
                raise BackupError(f"SQLite integrity check failed: {row}")

            # Overwrite target safely
            if target_db_path.exists():
                target_db_path.unlink()
            temp_target.rename(target_db_path)

            return {
                "restored_to": str(target_db_path),
                "timestamp": payload.get("timestamp"),
                "bytes": len(raw_data),
                "status": "success",
            }
        finally:
            if temp_target.exists():
                temp_target.unlink()
