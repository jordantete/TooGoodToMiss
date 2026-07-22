import json
import os
import threading
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import pytz
from freezegun import freeze_time

from app.core.state import StateStore, TgtgCredentials


class TestStateStore(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.path = Path(self._tmp.name) / "state.json"

    def tearDown(self):
        self._tmp.cleanup()

    def _store(self, **env):
        defaults = {
            "ACCESS_TOKEN": "seed-access",
            "REFRESH_TOKEN": "seed-refresh",
            "TGTG_COOKIE": "seed-cookie",
            "LAST_TIME_TOKEN_REFRESHED": "2026-07-22T08:00:00+00:00",
            "USER_LANGUAGE": "fr",
        }
        defaults.update(env)
        with patch.dict(os.environ, defaults, clear=False):
            return StateStore(self.path)

    def test_seeds_from_env_when_file_absent(self):
        store = self._store()
        creds = store.get_tgtg_credentials()
        self.assertEqual(creds.access_token, "seed-access")
        self.assertEqual(creds.refresh_token, "seed-refresh")
        self.assertEqual(creds.cookie, "seed-cookie")
        self.assertEqual(store.get_language(), "fr")
        self.assertTrue(self.path.exists())

    def test_seed_is_one_way_env_ignored_once_file_exists(self):
        self._store()
        # The .env changes, but state.json is now the source of truth.
        store = self._store(ACCESS_TOKEN="brand-new-token")
        self.assertEqual(store.get_tgtg_credentials().access_token, "seed-access")

    def test_file_is_created_with_0600_permissions(self):
        self._store()
        self.assertEqual(os.stat(self.path).st_mode & 0o777, 0o600)

    def test_save_credentials_persists_across_instances(self):
        store = self._store()
        store.save_tgtg_credentials(
            TgtgCredentials(
                access_token="new-access",
                refresh_token="new-refresh",
                cookie="new-cookie",
                last_time_token_refreshed="2026-07-22T12:00:00+00:00",
            )
        )
        reloaded = StateStore(self.path)
        self.assertEqual(reloaded.get_tgtg_credentials().access_token, "new-access")

    def test_write_is_atomic_no_tmp_file_left_behind(self):
        store = self._store()
        store.set_language("en")
        self.assertFalse(Path(str(self.path) + ".tmp").exists())

    def test_corrupted_state_file_is_reseeded_not_fatal(self):
        self.path.write_text("{ this is not json", encoding="utf-8")
        store = self._store()
        self.assertEqual(store.get_tgtg_credentials().access_token, "seed-access")

    def test_valid_json_but_non_dict_root_is_reseeded_not_fatal(self):
        self.path.write_text("[1, 2, 3]", encoding="utf-8")
        store = self._store()
        self.assertEqual(store.get_tgtg_credentials().access_token, "seed-access")

    def test_permissions_stay_0600_after_second_write(self):
        store = self._store()
        store.set_language("en")
        self.assertEqual(os.stat(self.path).st_mode & 0o777, 0o600)

    def test_orphan_tmp_with_loose_permissions_does_not_affect_new_writes(self):
        """Regression test: a stale orphan tmp file (e.g. left behind by a crash)
        with loose permissions must not affect the permissions of state.json -
        each write uses its own unique temp file, not a shared/reused one."""
        orphan_path = self.path.parent / f"{self.path.name}.tmp.orphan"
        orphan_path.write_text("{}", encoding="utf-8")
        os.chmod(orphan_path, 0o644)
        self.assertEqual(os.stat(orphan_path).st_mode & 0o777, 0o644)

        # Now instantiate StateStore, which triggers one write (via seed)
        store = self._store()

        # The orphan is untouched (unrelated file), state.json is still 0600
        self.assertEqual(os.stat(orphan_path).st_mode & 0o777, 0o644)
        self.assertEqual(os.stat(self.path).st_mode & 0o777, 0o600, "state.json must be 0o600 regardless of unrelated orphan tmp files")

    @freeze_time("2026-07-22 10:00:00")
    def test_concurrent_writes_leave_state_file_valid_and_no_orphan_tmp(self):
        """Regression test: several threads writing through the same StateStore
        concurrently (e.g. /wakeup racing an in-flight monitoring pass) must
        never interleave into a corrupt state.json, and must never leave an
        orphan temp file behind.

        Uses mark_notified, which ADDS a new key to self._state["notifications"]
        on every call (unlike set_language, which only reassigns an existing
        key and never changes dict size - that variant never exercises the
        "dictionary changed size during iteration" path and passes even on
        buggy, non-thread-safe code)."""
        store = self._store()
        errors = []
        thread_count = 4
        writes_per_thread = 300

        def writer(thread_id: int) -> None:
            for i in range(writes_per_thread):
                try:
                    store.mark_notified(f"{thread_id}-{i}")
                except Exception as e:  # noqa: BLE001 - capturing to fail the test, not swallow
                    errors.append(e)
                    return

        threads = [
            threading.Thread(target=writer, args=(thread_id,))
            for thread_id in range(thread_count)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        self.assertEqual(errors, [], f"concurrent writes raised: {errors}")

        # state.json must always be valid, parsable JSON after concurrent writes.
        on_disk = json.loads(self.path.read_text(encoding="utf-8"))

        # Every write from every thread must be present - proves none was lost.
        expected_keys = {
            f"{thread_id}-{i}"
            for thread_id in range(thread_count)
            for i in range(writes_per_thread)
        }
        self.assertEqual(set(on_disk.get("notifications", {})), expected_keys)

        # No leftover temp files from any of the writes.
        leftovers = list(self.path.parent.glob(f"{self.path.name}.tmp*"))
        self.assertEqual(leftovers, [])

    @freeze_time("2026-07-22 10:00:00")
    def test_cooldown_active_then_expired(self):
        store = self._store()
        self.assertFalse(store.is_paused())
        store.set_cooldown(30)
        self.assertTrue(store.is_paused())
        self.assertAlmostEqual(store.cooldown_remaining(), 1800, delta=2)
        with freeze_time("2026-07-22 10:31:00"):
            self.assertFalse(store.is_paused())
            self.assertIsNone(store.cooldown_remaining())

    @freeze_time("2026-07-22 10:00:00")
    def test_clear_cooldown(self):
        store = self._store()
        store.set_cooldown(30)
        store.clear_cooldown()
        self.assertFalse(store.is_paused())

    @freeze_time("2026-07-22 10:00:00")
    def test_notification_dedup_is_per_store_per_day(self):
        store = self._store()
        self.assertFalse(store.was_notified_today("4821"))
        store.mark_notified("4821")
        self.assertTrue(store.was_notified_today("4821"))
        self.assertFalse(store.was_notified_today("9999"))

    @freeze_time("2026-07-22 10:00:00")
    def test_stale_notifications_are_pruned_on_write(self):
        store = self._store()
        store.mark_notified("4821")
        with freeze_time("2026-07-23 10:00:00"):
            store.mark_notified("9999")
            self.assertFalse(store.was_notified_today("4821"))
            on_disk = json.loads(self.path.read_text(encoding="utf-8"))
            self.assertEqual(list(on_disk["notifications"]), ["9999"])

    def test_store_id_is_normalised_to_string(self):
        store = self._store()
        store.mark_notified(4821)
        self.assertTrue(store.was_notified_today("4821"))


if __name__ == "__main__":
    unittest.main()
