import sqlite3
import pytest
from pathlib import Path
from src.auth.backup import DatabaseBackupManager, BackupError

def test_encrypted_backup_and_restore(tmp_path: Path):
    db_file = tmp_path / "test.db"
    conn = sqlite3.connect(db_file)
    conn.execute("CREATE TABLE test (id INT, val TEXT)")
    conn.execute("INSERT INTO test VALUES (1, 'hello')")
    conn.commit()
    conn.close()

    passphrase = "SecretPassphrase123!"
    backup_file = tmp_path / "test_backup.enc"

    # Create backup
    res_path = DatabaseBackupManager.create_encrypted_backup(db_file, passphrase, backup_file)
    assert res_path.exists()

    # Wrong passphrase restore fails
    restore_file = tmp_path / "restored.db"
    with pytest.raises(BackupError, match="Authentication failed"):
        DatabaseBackupManager.verify_and_restore_backup(backup_file, "WrongPassword", restore_file)

    # Correct passphrase restore succeeds
    info = DatabaseBackupManager.verify_and_restore_backup(backup_file, passphrase, restore_file)
    assert info["status"] == "success"
    assert restore_file.exists()

    # Verify data in restored database
    conn2 = sqlite3.connect(restore_file)
    cursor = conn2.cursor()
    cursor.execute("SELECT val FROM test WHERE id=1")
    row = cursor.fetchone()
    conn2.close()
    assert row[0] == 'hello'
