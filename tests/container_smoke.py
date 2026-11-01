"""Phase 13: start-up smoke test of the data-directory layout – no Discord connection, no real data.

CI runs it inside the real image (read-only root filesystem, only /data writable, see python-syntax.yml):

    docker run --rm --read-only --tmpfs /tmp -e PULSE_DATA_DIR_INIT=1 -v <empty dir>:/data <image> \
        python tests/container_smoke.py

Locally (no Docker) tests/test_phase13_data_isolation.py runs it with PULSE_DATA_DIR pointing to a temp folder and
checks that nothing was written into the code folder. It exercises: the start checks of main.prepare_process,
init_runtime (databases, migrations, .session_secret), JSON writes, transcripts, uploads, a full backup with verify,
restore test and real restore, logging and the commit shown in the system status.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

CODE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CODE))
for secret in ("DISCORD_TOKEN", "DISCORD_CLIENT_SECRET", "PULSE_HOST_AGENT_TOKEN"):
    os.environ.pop(secret, None)


def main() -> int:
    data = Path(os.environ["PULSE_DATA_DIR"]).resolve()
    import main as pulse_main
    pulse_main.prepare_process()  # verify_data_dir (+ PULSE_DATA_DIR_INIT), runtime folders, TMPDIR, logging

    import pulse_paths
    assert pulse_paths.DATA_DIR == data, pulse_paths.DATA_DIR
    marker = pulse_paths.read_marker()
    assert marker and marker["state"] in {"initialized", "complete"}, marker

    import pulse_db
    import webserver as ws
    assert Path(pulse_db.DB_PATH).resolve().parent == data, pulse_db.DB_PATH
    ws.init_runtime()
    for name in ("pulse.db", ".session_secret", "backups", "logs/pulse.log"):
        assert (data / name).exists(), name

    ws.save_json(ws.CONFIG_FILE, {"smoke": True})
    assert json.loads((data / "config.json").read_text(encoding="utf-8")) == {"smoke": True}
    (data / "transcripts").mkdir(exist_ok=True)
    (data / "transcripts" / "T-1.txt").write_text("smoke", encoding="utf-8")
    ws.VOICE_SUPPORT_AUDIO_DIR.mkdir(parents=True, exist_ok=True)
    (ws.VOICE_SUPPORT_AUDIO_DIR / "1_support_opening_x.mp3").write_bytes(b"ID3" + b"\0" * 200)
    (data / "assets").mkdir(exist_ok=True)
    (data / "assets" / "support_wait.mp3").write_bytes(b"ID3" + b"\0" * 200)

    import pulse_backup
    path, manifest = pulse_backup.create_full_backup(ws.DATA_DIR, ws.full_backup_dir(), prefix="predeploy_")
    assert Path(path).resolve().parent == (data / "backups").resolve(), path
    assert not pulse_backup.verify_full_backup(path)
    names = {entry["name"] for entry in manifest["files"]}
    for expected in ("db/pulse.db", "json/config.json", "files/transcripts/T-1.txt", "files/assets/support_wait.mp3"):
        assert expected in names, expected
    report = pulse_backup.restore_test(path)
    assert report.get("ok"), report
    ws.save_json(ws.CONFIG_FILE, {"smoke": "changed"})
    pulse_backup.restore_full_backup(path, ws.DATA_DIR, ws.full_backup_dir())
    assert json.loads((data / "config.json").read_text(encoding="utf-8")) == {"smoke": True}

    import pulse_server_control as scc
    commit = scc.installed_commit(ws.BASE_DIR)
    expected_sha = os.getenv("PULSE_GIT_SHA", "")
    if expected_sha:
        assert commit.get("sha") == expected_sha and commit.get("source") == "build", commit

    if os.getenv("PULSE_SMOKE_EXPECT_READONLY_CODE") == "1":
        try:
            (CODE / "pulse_smoke_probe.txt").write_text("x", encoding="utf-8")
        except OSError:
            pass
        else:
            raise AssertionError("Code-Verzeichnis ist beschreibbar")
    print(f"container smoke ok: data={data} files={len(manifest['files'])} commit={commit.get('short', '-')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
