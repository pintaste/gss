#!/usr/bin/env python3
"""Isolated unit tests for gas 1.2 (ccs-hardened, no real network)."""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GAS = ROOT / "gas"


def sample_auth(email: str = "alice@example.com", first: str = "Alice", last: str = "A") -> dict:
    return {
        "https://auth.x.ai::client-id": {
            "key": "fake-access-token",
            "auth_mode": "oauth",
            "create_time": "2026-01-01T00:00:00Z",
            "user_id": f"user-{email}",
            "email": email,
            "first_name": first,
            "last_name": last,
            "principal_type": "User",
            "principal_id": f"user-{email}",
            "refresh_token": f"refresh-{email}",
            "expires_at": "2026-12-31T00:00:00Z",
            "oidc_issuer": "https://auth.x.ai",
            "oidc_client_id": "client-id",
        }
    }


class GasTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.grok = self.base / "grok"
        self.switch = self.base / "switch"
        self.grok.mkdir()
        self.env = os.environ.copy()
        self.env["GROK_DIR"] = str(self.grok)
        self.env["GSS_HOME"] = str(self.switch)
        self.env["NO_COLOR"] = "1"
        self.env.pop("GAS_SILENT", None)
        self.env.pop("CCS_SILENT", None)
        self.env["PATH"] = str(ROOT) + os.pathsep + self.env.get("PATH", "")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def run_gas(self, *args: str, check: bool = True, input_text: str | None = None, env=None):
        return subprocess.run(
            [sys.executable, str(GAS), *args],
            env=env or self.env,
            capture_output=True,
            text=True,
            input=input_text,
            check=check,
        )

    def write_active(self, auth: dict) -> None:
        path = self.grok / "auth.json"
        path.write_text(json.dumps(auth), encoding="utf-8")
        os.chmod(path, 0o600)

    def seq(self) -> dict:
        return json.loads((self.switch / "sequence.json").read_text())

    def add_two(self) -> None:
        self.write_active(sample_auth("a@x.com", "A", "A"))
        self.run_gas("add")
        self.write_active(sample_auth("b@x.com", "B", "B"))
        self.run_gas("add")

    def test_version(self):
        r = self.run_gas("version")
        self.assertIn("gas 0.1", r.stdout)

    def test_add_ls_whoami(self):
        self.write_active(sample_auth())
        r = self.run_gas("add")
        self.assertIn("added Account-1", r.stdout)
        self.assertTrue((self.switch / "accounts" / "1" / "auth.json").exists())
        mode = (self.switch / "accounts" / "1" / "auth.json").stat().st_mode
        self.assertEqual(stat.S_IMODE(mode), 0o600)
        self.assertIn("(active)", self.run_gas("ls").stdout)
        self.assertEqual(self.run_gas("whoami").stdout.strip(), "alice@example.com")

    def test_add_updates_existing_email(self):
        self.write_active(sample_auth())
        self.run_gas("add")
        a = sample_auth()
        a["https://auth.x.ai::client-id"]["refresh_token"] = "refreshed"
        self.write_active(a)
        r = self.run_gas("add")
        self.assertIn("updated Account-1", r.stdout)
        stored = json.loads((self.switch / "accounts" / "1" / "auth.json").read_text())
        self.assertEqual(next(iter(stored.values()))["refresh_token"], "refreshed")
        self.assertEqual(len(self.seq()["accounts"]), 1)

    def test_sw_and_to(self):
        self.add_two()
        self.run_gas("to", "1")
        self.assertEqual(self.run_gas("whoami").stdout.strip(), "a@x.com")
        self.run_gas("sw")
        self.assertEqual(self.run_gas("whoami").stdout.strip(), "b@x.com")
        self.run_gas("sw")
        self.assertEqual(self.run_gas("whoami").stdout.strip(), "a@x.com")

    def test_to_by_email_and_profile(self):
        self.add_two()
        self.run_gas("profile", "1", "personal")
        self.run_gas("profile", "2", "work")
        self.run_gas("to", "personal")
        self.assertEqual(self.run_gas("whoami").stdout.strip(), "a@x.com")
        self.run_gas("to", "b@x.com")
        self.assertEqual(self.run_gas("whoami").stdout.strip(), "b@x.com")

    def test_dry_run_does_not_switch(self):
        self.add_two()
        before = (self.grok / "auth.json").read_text()
        r = self.run_gas("-n", "to", "1")
        self.assertIn("[DRY RUN]", r.stdout)
        self.assertEqual((self.grok / "auth.json").read_text(), before)

    def test_switch_backs_up_refreshed_token(self):
        self.add_two()
        refreshed = sample_auth("b@x.com", "B", "B")
        refreshed["https://auth.x.ai::client-id"]["refresh_token"] = "live-refreshed-b"
        self.write_active(refreshed)
        self.run_gas("to", "1")
        stored_b = json.loads((self.switch / "accounts" / "2" / "auth.json").read_text())
        self.assertEqual(next(iter(stored_b.values()))["refresh_token"], "live-refreshed-b")

    def test_unmanaged_live_rejects_switch(self):
        """ccs 0.4: switching while live is unmanaged must refuse (don't lose creds)."""
        self.write_active(sample_auth("a@x.com"))
        self.run_gas("add")
        # live becomes unmanaged account
        self.write_active(sample_auth("orphan@x.com", "O", "O"))
        r = self.run_gas("to", "1", check=False)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("not managed", r.stderr)
        # orphan live must remain (not overwritten)
        self.assertEqual(self.run_gas("whoami").stdout.strip(), "orphan@x.com")

    def test_sw_auto_adds_unmanaged_then_requires_rerun(self):
        self.write_active(sample_auth("a@x.com"))
        self.run_gas("add")
        self.write_active(sample_auth("c@x.com", "C", "C"))
        r = self.run_gas("sw")
        self.assertIn("was not managed", r.stdout)
        self.assertIn("Account-2", r.stdout)
        # still on c until second sw
        self.assertEqual(self.run_gas("whoami").stdout.strip(), "c@x.com")
        self.run_gas("sw")
        self.assertEqual(self.run_gas("whoami").stdout.strip(), "a@x.com")

    def test_lock_serializes_switch(self):
        """Second switch waits/fails if lock held; after release works."""
        self.add_two()
        lock = self.switch / ".switch.lock"
        lock.mkdir()
        (lock / "pid").write_text("1")  # PID 1 usually exists... use fake dead pid
        # Use a high dead pid that won't be alive
        (lock / "pid").write_text("99999999")
        # Should steal stale lock and succeed
        r = self.run_gas("to", "1")
        self.assertIn("switched", r.stdout)
        self.assertFalse(lock.exists())  # released

    def test_lock_busy_with_live_pid(self):
        self.add_two()
        lock = self.switch / ".switch.lock"
        lock.mkdir()
        (lock / "pid").write_text(str(os.getpid()))  # this process is alive
        env = self.env.copy()
        env["GAS_LOCK_TIMEOUT_SECS"] = "0.4"
        r = self.run_gas("to", "1", check=False, env=env)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("lock", r.stderr.lower())
        import shutil

        shutil.rmtree(lock, ignore_errors=True)

    def test_silent_mode(self):
        self.add_two()
        env = self.env.copy()
        env["GAS_SILENT"] = "1"
        r = self.run_gas("to", "1", env=env)
        self.assertEqual(r.stdout.strip(), "")
        self.assertEqual(self.run_gas("whoami").stdout.strip(), "a@x.com")

    def test_config_dir_and_exec(self):
        self.add_two()
        r = self.run_gas("config-dir", "1")
        self.assertIn("export GROK_HOME=", r.stdout)
        # last line is export
        export_line = [ln for ln in r.stdout.strip().splitlines() if ln.startswith("export ")][-1]
        home = export_line.split("=", 1)[1].strip().strip('"')
        self.assertTrue(Path(home).joinpath("auth.json").exists())
        auth = json.loads(Path(home).joinpath("auth.json").read_text())
        self.assertEqual(next(iter(auth.values()))["email"], "a@x.com")

        # exec: run python that prints GROK_HOME and auth email
        script = (
            "import os,json; "
            "print(os.environ['GROK_HOME']); "
            "print(json.load(open(os.path.join(os.environ['GROK_HOME'],'auth.json')))"
            "[list(json.load(open(os.path.join(os.environ['GROK_HOME'],'auth.json'))).keys())[0]]['email'])"
        )
        # simpler script
        script = (
            "import os,json,pathlib;"
            "h=os.environ['GROK_HOME'];"
            "a=json.loads(pathlib.Path(h,'auth.json').read_text());"
            "print(next(iter(a.values()))['email'])"
        )
        r = self.run_gas("exec", "1", "--", sys.executable, "-c", script)
        self.assertEqual(r.returncode, 0, msg=r.stderr + r.stdout)
        self.assertIn("a@x.com", r.stdout)

    def test_exec_dry_run(self):
        self.add_two()
        r = self.run_gas("-n", "exec", "1", "--", "echo", "hi")
        self.assertIn("[DRY RUN]", r.stdout)

    def test_rm_and_check_status(self):
        self.write_active(sample_auth())
        self.run_gas("add")
        self.run_gas("profile", "1", "main")
        self.assertEqual(self.seq()["accounts"]["1"]["profile"], "main")
        self.assertIn("All checks passed", self.run_gas("check").stdout)
        self.assertIn("alice@example.com", self.run_gas("status").stdout)
        self.run_gas("rm", "1", "-y")
        self.assertEqual(self.seq()["accounts"], {})

    def test_dir_auto(self):
        self.add_two()
        work = self.base / "workproj"
        work.mkdir()
        self.run_gas("dir", str(work), "1")
        r = subprocess.run(
            [sys.executable, str(GAS), "auto"],
            env=self.env,
            cwd=str(work),
            capture_output=True,
            text=True,
            check=True,
        )
        self.assertIn("switched", r.stdout)
        self.assertEqual(self.run_gas("whoami").stdout.strip(), "a@x.com")

    def test_legacy_save_use(self):
        self.write_active(sample_auth())
        self.run_gas("save", "personal")
        self.assertEqual(self.seq()["accounts"]["1"]["profile"], "personal")
        self.write_active(sample_auth("b@x.com", "B", "B"))
        self.run_gas("add")
        self.run_gas("use", "personal")
        self.assertEqual(self.run_gas("whoami").stdout.strip(), "alice@example.com")

    def test_migrate_v1_profiles(self):
        pdir = self.switch / "profiles" / "personal"
        pdir.mkdir(parents=True)
        auth = sample_auth("old@x.com")
        (pdir / "auth.json").write_text(json.dumps(auth))
        (pdir / "meta.json").write_text(json.dumps({"email": "old@x.com"}))
        (self.switch / "state.json").write_text(json.dumps({"current": "personal"}))
        self.write_active(auth)
        r = self.run_gas("ls")
        self.assertIn("old@x.com", r.stdout)
        self.assertTrue((self.switch / "sequence.json").exists())

    def test_add_without_auth_fails(self):
        r = self.run_gas("add", check=False)
        self.assertNotEqual(r.returncode, 0)

    def test_stats(self):
        self.add_two()
        self.run_gas("to", "1")
        r = self.run_gas("stats")
        self.assertIn("Switches", r.stdout)


if __name__ == "__main__":
    unittest.main()
