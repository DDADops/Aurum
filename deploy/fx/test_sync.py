"""Host deployment tests: python3 -m unittest discover -s deploy/fx -v."""
import contextlib
import fcntl
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
spec = importlib.util.spec_from_file_location("fx_host_sync", HERE / "sync.py")
sync = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sync)
SECRET = "a" * 32


def result(code=0):
    report = {key: 0 for key in ("observations_inserted", "observations_to_insert",
                               "identical_observations", "conflicting_observations", "missing_observations")}
    report.update(exit_code=code, errors=[SECRET])  # Never relay free-form errors.
    return subprocess.CompletedProcess([], code, json.dumps(report), SECRET)


class DeploymentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.state = Path(self.temp.name)
        env = patch.dict(os.environ, {
            "AURUM_OXR_APP_ID": SECRET, "AURUM_FX_COMPOSE_PROJECT": "aurum",
            "AURUM_FX_CURRENCIES": "RUB,EUR,VND,CNY", "AURUM_FX_START_DATE": "2020-01-01",
            "AURUM_FX_STATE_DIR": self.temp.name,
        }, clear=True)
        env.start()
        self.addCleanup(env.stop)
        self.runner = patch.object(sync.subprocess, "run", return_value=result()).start()
        self.addCleanup(patch.stopall)

    def call(self, args=()):
        output = io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            code = sync.main(list(args))
        self.assertNotIn(SECRET, output.getvalue())
        return code, output.getvalue()

    def test_normal_backend_cmd_unchanged_and_no_entrypoint(self):
        dockerfile = (ROOT / "backend/Dockerfile").read_text()
        self.assertNotIn("\nENTRYPOINT", dockerfile)
        self.assertIn('CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000"]', dockerfile)
        compose = (ROOT / "docker-compose.yml").read_text()
        backend = compose.split("  backend:\n")[1].split("  web:\n")[0]
        self.assertNotIn("    command:", backend)
        self.assertNotIn("    entrypoint:", backend)
        self.assertNotIn("AURUM_OXR_APP_ID:", backend)

    def test_command_overrides_cmd_and_passes_secret_by_name_only(self):
        self.assertEqual(self.call(["--from", "2020-01-01", "--to", "2020-01-02", "--dry-run"])[0], 0)
        args = self.runner.call_args.args[0]
        self.assertEqual(args[args.index("backend") + 1:][:3], ["python", "-m", "scripts.sync_fx"])
        self.assertIn("--no-deps", args)
        self.assertIn("--rm", args)
        self.assertIn("--dry-run", args)
        self.assertEqual(args[args.index("-e") + 1], "AURUM_OXR_APP_ID")
        self.assertEqual(args[args.index("--currencies") + 1], "RUB,EUR,VND,CNY")
        self.assertNotIn(SECRET, str(args))
        self.assertNotIn("uvicorn", args)
        self.assertNotIn("alembic", args)
        self.assertEqual(self.runner.call_args.kwargs["stderr"], subprocess.DEVNULL)
        self.assertFalse((self.state / "cursor.json").exists())

    def test_missed_dates_recovered_in_bounded_batches(self):
        self.assertEqual(self.call()[0], 0)
        saved = json.loads((self.state / "cursor.json").read_text())
        self.assertEqual(saved["through"], "2020-01-31")
        self.assertEqual(self.call()[0], 0)
        args = self.runner.call_args.args[0]
        self.assertEqual(args[args.index("--from") + 1], "2020-02-01")
        self.assertEqual(args[args.index("--to") + 1], "2020-03-02")

    def test_only_completed_utc_dates_and_no_work_when_current(self):
        yesterday = sync.datetime.now(sync.timezone.utc).date() - sync.timedelta(days=1)
        os.environ["AURUM_FX_START_DATE"] = yesterday.isoformat()
        self.assertEqual(self.call()[0], 0)
        args = self.runner.call_args.args[0]
        self.assertEqual(args[args.index("--to") + 1], yesterday.isoformat())
        self.runner.reset_mock()
        self.assertEqual(self.call()[0], 0)
        self.runner.assert_not_called()

    def test_cli_failure_codes_propagate_without_advancing_cursor(self):
        for code in range(1, 7):
            with self.subTest(code=code):
                self.runner.return_value = result(code)
                self.assertEqual(self.call()[0], code)
                self.assertFalse((self.state / "cursor.json").exists())

    def test_docker_diagnostics_not_relayed(self):
        self.runner.return_value = subprocess.CompletedProcess([], 125, SECRET, SECRET)
        self.assertEqual(self.call()[0], 125)

    def test_invalid_success_report_cannot_advance_cursor(self):
        self.runner.return_value = subprocess.CompletedProcess([], 0, SECRET, SECRET)
        self.assertEqual(self.call()[0], 70)
        self.assertFalse((self.state / "cursor.json").exists())

    def test_failure_retries_same_dates_after_existing_success(self):
        self.assertEqual(self.call()[0], 0)
        saved = (self.state / "cursor.json").read_text()
        self.runner.return_value = result(4)
        self.assertEqual(self.call()[0], 4)
        failed_args = self.runner.call_args.args[0]
        self.assertEqual((self.state / "cursor.json").read_text(), saved)
        self.runner.return_value = result()
        self.assertEqual(self.call()[0], 0)
        self.assertEqual(self.runner.call_args.args[0], failed_args)

    def test_unknown_arguments_are_not_echoed(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            with self.assertRaises(SystemExit) as raised:
                sync.main(["--unknown", SECRET])
        self.assertEqual(raised.exception.code, 2)
        self.assertNotIn(SECRET, output.getvalue())
        self.runner.assert_not_called()

    def test_overlapping_run_and_restore_lock_block_execution(self):
        with (self.state / "run.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.assertEqual(self.call()[0], 75)
        self.runner.assert_not_called()

    def test_restore_marker_blocks_after_reboot_even_without_lock(self):
        (self.state / "restore-in-progress").touch()
        self.assertEqual(self.call()[0], 75)
        self.runner.assert_not_called()

    def test_dry_run_and_manual_write_never_advance_scheduled_cursor(self):
        self.assertEqual(self.call(["--dry-run"])[0], 0)
        self.assertEqual(self.call(["--from", "2020-01-01", "--to", "2020-01-02"])[0], 0)
        self.assertFalse((self.state / "cursor.json").exists())

    def test_invalid_dates_and_missing_explicit_currencies(self):
        today = sync.datetime.now(sync.timezone.utc).date().isoformat()
        for args in (["--from", today, "--to", today], ["--from", "2020-01-01"],
                     ["--from", "2020-01-01", "--to", "2020-02-01"]):
            self.assertEqual(self.call(args)[0], 2)
        del os.environ["AURUM_FX_CURRENCIES"]
        self.assertEqual(self.call()[0], 2)
        self.runner.assert_not_called()

    def test_changed_currencies_fail_closed_until_cursor_reviewed(self):
        self.assertEqual(self.call()[0], 0)
        self.runner.reset_mock()
        os.environ["AURUM_FX_CURRENCIES"] = "EUR"
        self.assertEqual(self.call()[0], 2)
        self.runner.assert_not_called()

    def test_systemd_configuration(self):
        service = (HERE / "aurum-fx.service").read_text()
        timer = (HERE / "aurum-fx.timer").read_text()
        self.assertIn("Type=oneshot", service)
        self.assertIn("EnvironmentFile=/etc/aurum/fx.env", service)
        self.assertIn("UMask=0077", service)
        self.assertNotIn("AURUM_OXR_APP_ID", service)
        self.assertIn("Persistent=true", timer)
        self.assertIn("OnCalendar=*-*-* 03:15:00 UTC", timer)
        self.assertIn("Unit=aurum-fx.service", timer)


if __name__ == "__main__":
    unittest.main()
