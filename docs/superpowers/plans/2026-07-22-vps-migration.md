# Migration AWS Lambda → VPS — Plan d'implémentation

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Supprimer toute dépendance à AWS et Serverless, et faire tourner le bot comme un process unique long sur un VPS, déployable par `scripts/deploy.sh`.

**Architecture:** Un seul process Python piloté par `Application.run_polling()` de python-telegram-bot. Le monitoring devient un job PTB qui se replanifie lui-même avec le jitter anti-fingerprinting existant, en remplacement des règles EventBridge one-shot. Un fichier `state.json` en écriture atomique remplace à la fois la table DynamoDB et les variables d'environnement Lambda mutables.

**Tech Stack:** Python 3.10, `python-telegram-bot[job-queue]` (PTB 21.10 + APScheduler), pydantic, pytz, requests, python-dotenv. Tests : `unittest` + `unittest.mock` + `freezegun` + pytest.

**Spec :** `docs/superpowers/specs/2026-07-22-vps-migration-design.md`

## Global Constraints

- Python 3.10. Environnement conda `TooGoodToMiss` jusqu'à la Task 4, venv ensuite.
- `requirements.txt` doit spécifier **`python-telegram-bot[job-queue]`** — sans l'extra, `application.job_queue` vaut `None` et le monitoring ne démarre jamais.
- **Aucun `import boto3`** ne doit subsister à la fin de la Task 2.
- **Ne jamais logger la valeur** d'un token, d'un refresh token ou d'un cookie. Logger l'événement, pas le secret.
- Style de signature multi-ligne (un paramètre par ligne) — respecter l'existant.
- Logging uniquement via `app.common.logger.LOGGER`.
- Fenêtres de randomisation à **conserver à l'identique** : matin 10h–12h → délai 10–20 min ; après-midi 12h–19h → délai 2–5 min ; pas d'exécution le dimanche. C'est de l'anti-fingerprinting TGTG.
- Tests : classes `unittest.TestCase`, `unittest.mock`, `freezegun` pour le temps, fixtures partagées dans `tests/conftest.py`. Jamais de `moto` — les clients boto3 étaient mockés, et disparaissent.
- Commande de test : `conda run -n TooGoodToMiss python -m pytest tests/ -q`

## ⚠️ Le piège architectural n°1 : la boucle qui s'arrête toute seule

Sur Lambda, **deux** planificateurs coexistaient : un cron fixe (`*/3 10-19 MON-SAT`) sur `too-good-to-miss-scheduler`, et les règles EventBridge one-shot. Si `schedule_next_invocation()` décidait de ne rien planifier (dimanche, hors fenêtre), le cron fixe repassait 3 minutes plus tard et relançait la machine. Le `return None` de `_calculate_next_invocation_time` était donc **rattrapable**.

Sur le VPS il n'y a plus de filet. Le job se replanifie lui-même et rien d'autre ne le réarme. **Si `monitor_job` ne planifie pas son successeur, le monitoring s'arrête définitivement jusqu'au prochain redémarrage du process** — silencieusement, sans erreur, et un dimanche soir personne ne s'en aperçoit.

Conséquence contraignante pour la Task 2 : `next_delay_seconds()` doit **toujours** retourner un délai. Hors fenêtre, le dimanche, ou en cooldown, elle retourne un délai de re-vérification (`OFF_WINDOW_RETRY_MINUTES`), jamais `None`. C'est le comportement testé par `test_next_delay_never_returns_none`.

---

## Task 1 : StateStore et déduplication

**Files:**
- Modify: `.gitignore`
- Create: `app/core/state.py`
- Test: `tests/test_state.py`

**Interfaces:**
- Consumes: `app.common.utils.Utils.get_environment_variable`, `app.common.logger.LOGGER`
- Produces:
  - `TgtgCredentials` dataclass — champs `access_token: Optional[str]`, `refresh_token: Optional[str]`, `cookie: Optional[str]`, `last_time_token_refreshed: Optional[str]` (ISO 8601, **str et non datetime**)
  - `StateStore(path: Path = DEFAULT_STATE_PATH)` avec : `get_tgtg_credentials() -> TgtgCredentials`, `save_tgtg_credentials(credentials: TgtgCredentials) -> None`, `is_paused() -> bool`, `cooldown_remaining() -> Optional[float]`, `set_cooldown(minutes: int) -> None`, `clear_cooldown() -> None`, `get_language() -> str`, `set_language(language: str) -> None`, `was_notified_today(store_id: str) -> bool`, `mark_notified(store_id: str) -> None`

- [ ] **Step 1 : Protéger l'état avant d'écrire la moindre ligne de code**

L'ordre compte. Ajouter à la fin de la section `# Secrets` de `.gitignore` :

```gitignore
# Secrets
.env
.env.yml

# Runtime state (contient les tokens TGTG en clair)
state.json
state.json.tmp
logs/
```

- [ ] **Step 2 : Commiter le .gitignore seul**

```bash
git add .gitignore
git commit -m "chore: ignorer state.json et logs avant d'introduire la couche d'etat"
```

- [ ] **Step 3 : Écrire les tests qui échouent**

Créer `tests/test_state.py` :

```python
import json
import os
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
        # Le .env change, mais state.json fait desormais autorite.
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
```

- [ ] **Step 4 : Vérifier que les tests échouent**

Run : `conda run -n TooGoodToMiss python -m pytest tests/test_state.py -q`
Expected : FAIL — `ModuleNotFoundError: No module named 'app.core.state'`

- [ ] **Step 5 : Implémenter StateStore**

Créer `app/core/state.py` :

```python
import json, os, pytz
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional
from app.common.logger import LOGGER
from app.common.utils import Utils

DEFAULT_STATE_PATH = Path(os.getenv("STATE_FILE", "state.json"))

@dataclass
class TgtgCredentials:
    access_token: Optional[str]
    refresh_token: Optional[str]
    cookie: Optional[str]
    last_time_token_refreshed: Optional[str]

class StateStore:
    """Persistent runtime state: TGTG session, cooldown, language, notification dedup."""

    def __init__(
        self,
        path: Path = DEFAULT_STATE_PATH
    ):
        self.path = Path(path)
        self._state = self._load()

    def _load(self) -> dict:
        """Load state.json, seeding it from the environment on first run."""
        if not self.path.exists():
            state = self._seed_from_env()
            self._write(state)
            return state

        try:
            with self.path.open("r", encoding="utf-8") as state_file:
                return json.load(state_file)

        except (json.JSONDecodeError, OSError) as e:
            LOGGER.error(f"Unreadable state file at {self.path}: {e}. Re-seeding from environment.")
            state = self._seed_from_env()
            self._write(state)
            return state

    @staticmethod
    def _seed_from_env() -> dict:
        """Build the initial state from environment variables. Runs ONCE, at creation."""
        return {
            "tgtg": {
                "access_token": Utils.get_environment_variable("ACCESS_TOKEN"),
                "refresh_token": Utils.get_environment_variable("REFRESH_TOKEN"),
                "cookie": Utils.get_environment_variable("TGTG_COOKIE"),
                "last_time_token_refreshed": Utils.get_environment_variable("LAST_TIME_TOKEN_REFRESHED"),
            },
            "cooldown_end_time": None,
            "user_language": Utils.get_environment_variable("USER_LANGUAGE", default="en"),
            "notifications": {},
        }

    @staticmethod
    def _today() -> str:
        return datetime.now(pytz.utc).date().isoformat()

    def _prune_notifications(
        self,
        state: dict
    ) -> None:
        """Drop notification entries older than today, bounding the file size."""
        today = self._today()
        state["notifications"] = {
            store_id: notified_on
            for store_id, notified_on in state.get("notifications", {}).items()
            if notified_on == today
        }

    def _write(
        self,
        state: dict
    ) -> None:
        """Write atomically: temp file then os.replace, so a crash never truncates state.json."""
        self._prune_notifications(state)
        tmp_path = Path(str(self.path) + ".tmp")
        with tmp_path.open("w", encoding="utf-8") as state_file:
            json.dump(state, state_file, indent=2)
        os.chmod(tmp_path, 0o600)
        os.replace(tmp_path, self.path)

    def _save(self) -> None:
        self._write(self._state)

    def get_tgtg_credentials(self) -> TgtgCredentials:
        """
        Return the current TGTG session.

        WARNING - the .env seed is ONE-WAY. Environment variables populate
        state.json only at its creation. Editing ACCESS_TOKEN in .env and
        redeploying has NO effect while state.json exists on the VPS. To
        force new tokens:

            ssh $VPS_USER@$VPS_HOST "rm $VPS_BOT_PATH/state.json"

        This is deliberate: deploy.sh must never overwrite a fresh session
        with a stale local copy, which would trigger a re-login and a CAPTCHA.
        """
        tgtg = self._state.get("tgtg", {})
        return TgtgCredentials(
            access_token=tgtg.get("access_token"),
            refresh_token=tgtg.get("refresh_token"),
            cookie=tgtg.get("cookie"),
            last_time_token_refreshed=tgtg.get("last_time_token_refreshed"),
        )

    def save_tgtg_credentials(
        self,
        credentials: TgtgCredentials
    ) -> None:
        self._state["tgtg"] = {
            "access_token": credentials.access_token,
            "refresh_token": credentials.refresh_token,
            "cookie": credentials.cookie,
            "last_time_token_refreshed": credentials.last_time_token_refreshed,
        }
        self._save()
        LOGGER.info("TGTG credentials refreshed and persisted.")

    def cooldown_remaining(self) -> Optional[float]:
        """Remaining cooldown in seconds, or None if no cooldown is active."""
        raw_end_time = self._state.get("cooldown_end_time")
        if not raw_end_time:
            return None

        end_time = datetime.fromisoformat(raw_end_time)
        if end_time.tzinfo is None:
            end_time = end_time.replace(tzinfo=pytz.utc)

        remaining = (end_time - datetime.now(pytz.utc)).total_seconds()
        return remaining if remaining > 0 else None

    def is_paused(self) -> bool:
        return self.cooldown_remaining() is not None

    def set_cooldown(
        self,
        minutes: int
    ) -> None:
        end_time = datetime.now(pytz.utc) + timedelta(minutes=minutes)
        self._state["cooldown_end_time"] = end_time.isoformat()
        self._save()
        LOGGER.info(f"Cooldown activated for {minutes} minutes.")

    def clear_cooldown(self) -> None:
        self._state["cooldown_end_time"] = None
        self._save()
        LOGGER.info("Cooldown cleared.")

    def get_language(self) -> str:
        return self._state.get("user_language", "en")

    def set_language(
        self,
        language: str
    ) -> None:
        self._state["user_language"] = language
        self._save()

    def was_notified_today(
        self,
        store_id: str
    ) -> bool:
        return self._state.get("notifications", {}).get(str(store_id)) == self._today()

    def mark_notified(
        self,
        store_id: str
    ) -> None:
        self._state.setdefault("notifications", {})[str(store_id)] = self._today()
        self._save()
```

- [ ] **Step 6 : Vérifier que les tests passent**

Run : `conda run -n TooGoodToMiss python -m pytest tests/test_state.py -q`
Expected : PASS — 11 passed

- [ ] **Step 7 : Vérifier qu'aucune régression n'est introduite**

Run : `conda run -n TooGoodToMiss python -m pytest tests/ -q`
Expected : 65 passed, 2 failed — les 2 échecs pré-existants de `tests/test_handlers.py` (`You must specify a region`). Ils disparaîtront en Task 2.

- [ ] **Step 8 : Commit**

```bash
git add app/core/state.py tests/test_state.py
git commit -m "feat: StateStore persiste tokens, cooldown, langue et dedup des notifs"
```

---

## Task 2 : Cœur applicatif — sortie complète d'AWS

**Important :** cette task est livrée en **un seul commit**, à la toute fin (Step 20). Les états intermédiaires ne tournent pas. Ne pas commiter avant que la suite complète soit verte.

**Files:**
- Modify: `app/core/scheduler.py` (réécriture, 214 → ~75 lignes)
- Modify: `app/services/tgtg_service/tgtg_service.py:7,30,42,55,64,83-135`
- Modify: `app/services/tgtg_service_monitor.py` (réécriture)
- Modify: `app/core/telegram_bot_handler.py:22-37,89-100,102-107,140-146,199-212,274-288`
- Modify: `app/common/utils.py` — supprimer `update_lambda_env_vars`, `ok_response`, `error_response`
- Modify: `app/services/telegram_service.py`
- Create: `app/main.py`
- Delete: `app/handlers.py`, `app/core/database_handler.py`, `app/core/exceptions.py`
- Delete: `tests/test_handlers.py`, `tests/test_database_handler.py`
- Create: `tests/test_main.py`
- Modify: `tests/test_scheduler.py` (réécriture), `tests/test_utils.py`, `tests/test_tgtg_service.py`, `tests/conftest.py`

**Interfaces:**
- Consumes: `StateStore`, `TgtgCredentials` (Task 1)
- Produces:
  - `Scheduler(state: StateStore)` avec `next_delay_seconds() -> float`, `should_monitor_now() -> bool`, `activate_cooldown(cooldown_minutes: int = 30) -> None`, `remove_cooldown() -> None`, `is_bot_paused() -> bool`, `cooldown_remaining() -> Optional[float]`
  - `TgtgServiceMonitor(state: StateStore)` avec `start_monitoring(scheduler: Scheduler) -> None`
  - `TgtgService(state: StateStore)` avec `get_favorites_items_list(...) -> List[ItemDetails]`, `get_notification_messages(item_details_list) -> List[str]`
  - `TelegramBotHandler(scheduler: Scheduler, state: StateStore)` exposant `application`
  - `app.main.build_application() -> Application`, `app.main.monitor_job(context) -> None`, `app.main.main() -> None`

- [ ] **Step 1 : Écrire les tests du scheduler (ils échouent)**

Remplacer intégralement `tests/test_scheduler.py` :

```python
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from freezegun import freeze_time

from app.core.scheduler import Scheduler
from app.core.state import StateStore


class TestScheduler(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.state = StateStore(Path(self._tmp.name) / "state.json")
        self.scheduler = Scheduler(self.state)

    def tearDown(self):
        self._tmp.cleanup()

    @freeze_time("2026-07-22 10:30:00")  # mercredi, fenetre du matin
    def test_morning_delay_between_10_and_20_minutes(self):
        for _ in range(50):
            delay = self.scheduler.next_delay_seconds()
            self.assertGreaterEqual(delay, 10 * 60)
            self.assertLessEqual(delay, 20 * 60)

    @freeze_time("2026-07-22 14:00:00")  # mercredi, fenetre de l'apres-midi
    def test_afternoon_delay_between_2_and_5_minutes(self):
        for _ in range(50):
            delay = self.scheduler.next_delay_seconds()
            self.assertGreaterEqual(delay, 2 * 60)
            self.assertLessEqual(delay, 5 * 60)

    @freeze_time("2026-07-26 14:00:00")  # dimanche
    def test_sunday_returns_retry_delay_not_none(self):
        delay = self.scheduler.next_delay_seconds()
        self.assertEqual(delay, Scheduler.OFF_WINDOW_RETRY_MINUTES * 60)

    @freeze_time("2026-07-22 03:00:00")  # hors fenetre
    def test_outside_window_returns_retry_delay(self):
        delay = self.scheduler.next_delay_seconds()
        self.assertEqual(delay, Scheduler.OFF_WINDOW_RETRY_MINUTES * 60)

    def test_next_delay_never_returns_none(self):
        """Garde-fou: un None arreterait definitivement la boucle de monitoring."""
        for hour in range(24):
            for day in ("2026-07-22", "2026-07-26"):  # mercredi et dimanche
                with freeze_time(f"{day} {hour:02d}:00:00"):
                    delay = self.scheduler.next_delay_seconds()
                    self.assertIsNotNone(delay)
                    self.assertGreater(delay, 0)

    @freeze_time("2026-07-22 10:30:00")
    def test_cooldown_delays_until_cooldown_expiry(self):
        self.scheduler.activate_cooldown(30)
        self.assertTrue(self.scheduler.is_bot_paused())
        delay = self.scheduler.next_delay_seconds()
        self.assertGreaterEqual(delay, 30 * 60)

    @freeze_time("2026-07-22 10:30:00")
    def test_remove_cooldown_resumes_normal_window(self):
        self.scheduler.activate_cooldown(30)
        self.scheduler.remove_cooldown()
        self.assertFalse(self.scheduler.is_bot_paused())
        self.assertLessEqual(self.scheduler.next_delay_seconds(), 20 * 60)

    @freeze_time("2026-07-22 10:30:00")
    def test_cooldown_survives_a_new_scheduler_instance(self):
        self.scheduler.activate_cooldown(30)
        self.assertTrue(Scheduler(self.state).is_bot_paused())


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2 : Vérifier l'échec**

Run : `conda run -n TooGoodToMiss python -m pytest tests/test_scheduler.py -q`
Expected : FAIL — `TypeError: Scheduler.__init__() takes 1 positional argument but 2 were given`

- [ ] **Step 3 : Réécrire le scheduler**

Remplacer intégralement `app/core/scheduler.py` :

```python
import pytz, random
from datetime import datetime
from typing import Optional, Tuple
from app.common.constants import WEEKDAY_MAP
from app.common.logger import LOGGER
from app.core.state import StateStore

class Scheduler:
    MORNING_WINDOW = ((10, 12), (10, 20))    # 10:00-12:00, delai 10-20 min
    AFTERNOON_WINDOW = ((12, 19), (2, 5))    # 12:00-19:00, delai 2-5 min
    OFF_WINDOW_RETRY_MINUTES = 30            # re-verification hors fenetre / dimanche
    COOLDOWN_MARGIN_SECONDS = 60             # marge pour ne pas se reveiller pile a l'expiration

    def __init__(
        self,
        state: StateStore
    ):
        self.state = state

    def _get_time_window(
        self,
        current_hour: int
    ) -> Optional[Tuple[Tuple[int, int], Tuple[int, int]]]:
        """Determine the active time window based on the current hour."""
        for window_name, ((start_hour, end_hour), delay_range) in {
            'morning': self.MORNING_WINDOW,
            'afternoon': self.AFTERNOON_WINDOW,
        }.items():
            if start_hour <= current_hour < end_hour:
                LOGGER.info(f"Current time falls within the {window_name} window.")
                return (start_hour, end_hour), delay_range
        return None

    def next_delay_seconds(self) -> float:
        """
        Seconds to wait before the next monitoring pass.

        NEVER returns None. On Lambda a fixed cron re-armed the machinery when
        no rule was scheduled; on the VPS the job is the only thing that
        reschedules itself, so returning None would stop monitoring for good.
        """
        cooldown_remaining = self.state.cooldown_remaining()
        if cooldown_remaining is not None:
            LOGGER.info(f"Cooldown active - next pass in {cooldown_remaining:.0f}s.")
            return cooldown_remaining + self.COOLDOWN_MARGIN_SECONDS

        now = datetime.now(pytz.utc)

        if WEEKDAY_MAP[now.weekday()] == 'Sunday':
            LOGGER.info("Today is Sunday - monitoring idle, will re-check later.")
            return self.OFF_WINDOW_RETRY_MINUTES * 60

        time_window = self._get_time_window(now.hour)
        if not time_window:
            LOGGER.info("Outside monitoring hours - will re-check later.")
            return self.OFF_WINDOW_RETRY_MINUTES * 60

        _, delay_range = time_window
        delay_minutes = random.randint(*delay_range)
        LOGGER.info(f"Next monitoring pass in {delay_minutes} minutes.")
        return delay_minutes * 60

    def activate_cooldown(
        self,
        cooldown_minutes: int = 30
    ) -> None:
        LOGGER.info("Triggering cooldown due to anti-bot detection.")
        self.state.set_cooldown(cooldown_minutes)

    def remove_cooldown(self) -> None:
        LOGGER.info("Removing cooldown and waking up the bot.")
        self.state.clear_cooldown()

    def is_bot_paused(self) -> bool:
        return self.state.is_paused()

    def cooldown_remaining(self) -> Optional[float]:
        return self.state.cooldown_remaining()

    def should_monitor_now(self) -> bool:
        """True when a monitoring pass should actually run right now."""
        if self.state.is_paused():
            return False

        now = datetime.now(pytz.utc)
        if WEEKDAY_MAP[now.weekday()] == 'Sunday':
            return False

        return self._get_time_window(now.hour) is not None
```

- [ ] **Step 4 : Vérifier que les tests du scheduler passent**

Run : `conda run -n TooGoodToMiss python -m pytest tests/test_scheduler.py -q`
Expected : PASS — 8 passed

- [ ] **Step 5 : Basculer TgtgService sur le StateStore et purger ses logs**

Dans `app/services/tgtg_service/tgtg_service.py` :

Remplacer l'import ligne 7-8 :

```python
from app.core.state import StateStore
```
(supprimer `from app.core.database_handler import DatabaseHandler` et `from app.core.exceptions import DatabaseQueryError`)

Remplacer `__init__` (lignes 29-31) :

```python
    def __init__(
        self,
        state: StateStore
    ):
        self.state = state
        self.credentials: Credentials = None
```

Remplacer la ligne 42 (fuite : email + 3 tokens) :

```python
        LOGGER.info("Logging in to TGTG API.")
```

Remplacer la ligne 55 (fuite : 3 tokens) :

```python
            LOGGER.info("TGTG client initialised.")
```

Remplacer la ligne 64 (fuite : credentials complets, à chaque requête) :

```python
            LOGGER.info("Local credentials updated after TGTG request.")
```

Remplacer `get_notification_messages`, `_is_notification_sent_today` et `_record_notification` (lignes 83-135) par :

```python
    def get_notification_messages(
        self,
        item_details_list: List[ItemDetails]
    ) -> List[str]:
        """Generate notification messages for available favorite items."""
        messages = []
        for item_details in item_details_list:
            store_id = str(item_details.store.store_id)

            if item_details.items_available > 0 and not self.state.was_notified_today(store_id):
                messages.append(NotificationFormatter.format_message(item_details))
                self.state.mark_notified(store_id)

        return messages
```

Supprimer aussi les imports devenus inutiles : `pytz`, `Dict`.

- [ ] **Step 6 : Réécrire TgtgServiceMonitor**

Remplacer intégralement `app/services/tgtg_service_monitor.py` :

```python
from app.common.logger import LOGGER
from app.common.utils import Utils
from app.core.scheduler import Scheduler
from app.core.state import StateStore, TgtgCredentials
from app.services.tgtg_service.exceptions import TgtgAPIConnectionError, TgtgAPIParsingError, ForbiddenError
from app.services.tgtg_service.tgtg_service import TgtgService

class TgtgServiceMonitor:
    def __init__(
        self,
        state: StateStore
    ):
        self.state = state
        self.user_email = Utils.get_environment_variable("USER_EMAIL")
        self.tgtg_service = TgtgService(state)

    def start_monitoring(
        self,
        scheduler: Scheduler
    ) -> None:
        """Run one monitoring pass if credentials look usable."""
        credentials = self.state.get_tgtg_credentials()

        has_session = credentials.access_token and credentials.refresh_token and credentials.cookie
        if not (self.user_email or has_session):
            LOGGER.error(
                "Missing credentials: set USER_EMAIL, or seed ACCESS_TOKEN / REFRESH_TOKEN / "
                "TGTG_COOKIE in .env before first start."
            )
            return None

        self._monitor_favorites(scheduler)

    def _has_session_changed(
        self,
        stored: TgtgCredentials
    ) -> bool:
        """Check whether the TGTG client rotated the session during this pass."""
        fresh = self.tgtg_service.credentials
        if fresh is None:
            return False
        return (
            fresh.access_token != stored.access_token
            or fresh.refresh_token != stored.refresh_token
        )

    def _monitor_favorites(
        self,
        scheduler: Scheduler
    ) -> None:
        """Check favorite items and send notifications if new items are available."""
        LOGGER.info("Checking favorite items and sending notifications if needed.")
        stored = self.state.get_tgtg_credentials()

        try:
            favorites = self.tgtg_service.get_favorites_items_list(
                self.user_email,
                stored.access_token,
                stored.refresh_token,
                stored.cookie,
                stored.last_time_token_refreshed
            )

            if self._has_session_changed(stored):
                fresh = self.tgtg_service.credentials
                self.state.save_tgtg_credentials(
                    TgtgCredentials(
                        access_token=fresh.access_token,
                        refresh_token=fresh.refresh_token,
                        cookie=fresh.cookie,
                        last_time_token_refreshed=fresh.get_last_time_token_refreshed_as_str(),
                    )
                )

            messages = self.tgtg_service.get_notification_messages(favorites)

            for message in messages:
                LOGGER.info("Sending Telegram notification.")
                Utils.send_telegram_message(message)

            if not messages:
                LOGGER.info("No new items available - no notifications sent.")

        except TgtgAPIParsingError as e:
            error_msg = f"TgtgAPIParsingError encountered: {str(e)}"
            LOGGER.error(error_msg)
            Utils.send_telegram_message(f"TgtgAPIParsingError: {error_msg}")

        except ForbiddenError as e:
            LOGGER.error(str(e))
            scheduler.activate_cooldown()
            Utils.send_telegram_message("API access forbidden. Monitoring paused temporarily.")

        except TgtgAPIConnectionError as e:
            LOGGER.error(f"Connection error to TGTG API. {str(e)}")
            Utils.send_telegram_message(f"TGTG API connection error: {str(e)}")

        except Exception as e:
            LOGGER.error(f"Unexpected error in _monitor_favorites: {str(e)}")
            Utils.send_telegram_message(f"TooGoodToMiss: Unexpected system error - {str(e)}")
```

- [ ] **Step 7 : Nettoyer Utils**

Dans `app/common/utils.py` :
- Supprimer `import boto3` (ligne 1).
- Supprimer `update_lambda_env_vars` (lignes 103-123) — c'était la source des deux fuites de tokens dans les logs.
- Supprimer `ok_response` (85-92) et `error_response` (94-101) — spécifiques à API Gateway.

- [ ] **Step 8 : Adapter TelegramBotHandler au StateStore**

Dans `app/core/telegram_bot_handler.py` :

Remplacer `__init__` (lignes 22-37) :

```python
    def __init__(
        self,
        scheduler: Scheduler,
        state: StateStore
    ):
        LOGGER.info("Initializing TelegramBotHandler")
        telegram_token = Utils.get_environment_variable("TELEGRAM_BOT_TOKEN")
        self.application = ApplicationBuilder().token(telegram_token).build()
        self.localizable_strings = Utils.load_localizable_data()
        self.chat_id = Utils.get_environment_variable("TELEGRAM_CHAT_ID")
        self.state = state
        self.scheduler = scheduler
        self._register_handlers()
        LOGGER.info(f"TelegramBotHandler initialized with: user_language={self.user_language}")
```

Ajouter l'import `from app.core.state import StateStore`.

Remplacer l'attribut `self.user_language` par une property lisant l'état (le fichier fait foi, plus la variable d'instance) :

```python
    @property
    def user_language(self) -> str:
        return self.state.get_language()
```

Remplacer `_bot_status_handler` ligne 91 :

```python
        remaining_time = self.scheduler.cooldown_remaining()
        if remaining_time is not None:
```

Remplacer `_handle_language_selection` lignes 207-209 :

```python
        self.state.set_language(selected_language)
```
(supprimer `new_env_vars` et l'appel `Utils.update_lambda_env_vars`)

Remplacer `start()` (lignes 274-288) par un hook de démarrage, branché sur le builder plutôt qu'assigné après coup :

```python
    async def _on_startup(
        self,
        application: Application
    ) -> None:
        """Register bot commands once, when the application starts."""
        await self._set_bot_commands()
```

Et brancher ce hook à la construction, dans `__init__` :

```python
        self.application = (
            ApplicationBuilder()
            .token(telegram_token)
            .post_init(self._on_startup)
            .build()
        )
```

Ajouter l'import `Application` : `from telegram.ext import Application, ApplicationBuilder, ...`

⚠️ `post_init` doit être passé **au builder**. L'assigner après `build()` fonctionne aujourd'hui mais repose sur un détail interne de PTB.

- [ ] **Step 9 : Supprimer les modules AWS**

```bash
git rm app/handlers.py app/core/database_handler.py app/core/exceptions.py
git rm tests/test_handlers.py tests/test_database_handler.py
```

`app/services/telegram_service.py` n'a plus de raison d'être (simple passe-plat vers le webhook) :

```bash
git rm app/services/telegram_service.py tests/test_telegram_service.py
```

- [ ] **Step 10 : Écrire les tests de main.py (ils échouent)**

Créer `tests/test_main.py` :

```python
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import AsyncMock, MagicMock, patch

from freezegun import freeze_time

from app.core.scheduler import Scheduler
from app.core.state import StateStore
from app.main import monitor_job


class TestMonitorJob(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.state = StateStore(Path(self._tmp.name) / "state.json")
        self.scheduler = Scheduler(self.state)

        self.context = MagicMock()
        self.context.job_queue.run_once = MagicMock()
        self.context.bot_data = {"state": self.state, "scheduler": self.scheduler}

    def tearDown(self):
        self._tmp.cleanup()

    @freeze_time("2026-07-22 10:30:00")
    async def test_runs_monitoring_and_reschedules_itself(self):
        with patch("app.main.TgtgServiceMonitor") as monitor_cls:
            await monitor_job(self.context)
            monitor_cls.return_value.start_monitoring.assert_called_once()
        self.context.job_queue.run_once.assert_called_once()

    @freeze_time("2026-07-22 10:30:00")
    async def test_skips_monitoring_while_paused_but_still_reschedules(self):
        self.scheduler.activate_cooldown(30)
        with patch("app.main.TgtgServiceMonitor") as monitor_cls:
            await monitor_job(self.context)
            monitor_cls.return_value.start_monitoring.assert_not_called()
        self.context.job_queue.run_once.assert_called_once()

    @freeze_time("2026-07-26 14:00:00")  # dimanche
    async def test_reschedules_on_sunday_without_monitoring(self):
        with patch("app.main.TgtgServiceMonitor") as monitor_cls:
            await monitor_job(self.context)
            monitor_cls.return_value.start_monitoring.assert_not_called()
        self.context.job_queue.run_once.assert_called_once()

    @freeze_time("2026-07-22 10:30:00")
    async def test_reschedules_even_when_monitoring_raises(self):
        """Garde-fou: une exception ne doit jamais tuer la boucle."""
        with patch("app.main.TgtgServiceMonitor") as monitor_cls:
            monitor_cls.return_value.start_monitoring.side_effect = RuntimeError("boom")
            await monitor_job(self.context)
        self.context.job_queue.run_once.assert_called_once()


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 11 : Vérifier l'échec**

Run : `conda run -n TooGoodToMiss python -m pytest tests/test_main.py -q`
Expected : FAIL — `ModuleNotFoundError: No module named 'app.main'`

- [ ] **Step 12 : Écrire main.py**

Créer `app/main.py` :

```python
import asyncio
from dotenv import load_dotenv
from telegram.ext import Application, ContextTypes
from app.common.logger import LOGGER
from app.core.scheduler import Scheduler
from app.core.state import StateStore, DEFAULT_STATE_PATH
from app.core.telegram_bot_handler import TelegramBotHandler
from app.services.tgtg_service_monitor import TgtgServiceMonitor

async def monitor_job(
    context: ContextTypes.DEFAULT_TYPE
) -> None:
    """
    One monitoring pass, then reschedule itself.

    The reschedule is in a finally block on purpose: this job is the ONLY
    thing that re-arms monitoring. If it ever returns without scheduling a
    successor, monitoring stops until the process restarts.
    """
    state: StateStore = context.bot_data["state"]
    scheduler: Scheduler = context.bot_data["scheduler"]

    try:
        if not scheduler.should_monitor_now():
            LOGGER.info("Monitoring skipped - bot paused, outside window, or Sunday.")
        else:
            monitor = TgtgServiceMonitor(state)
            await asyncio.to_thread(monitor.start_monitoring, scheduler)

    except Exception as e:
        LOGGER.error(f"Monitoring pass failed: {e}")

    finally:
        delay = scheduler.next_delay_seconds()
        context.job_queue.run_once(monitor_job, when=delay, name="monitoring")
        LOGGER.info(f"Next monitoring pass scheduled in {delay:.0f}s.")

def build_application() -> Application:
    """Wire the state store, scheduler, Telegram handlers and monitoring job."""
    state = StateStore(DEFAULT_STATE_PATH)
    scheduler = Scheduler(state)
    bot_handler = TelegramBotHandler(scheduler, state)

    application = bot_handler.application

    if application.job_queue is None:
        raise RuntimeError(
            "application.job_queue is None - install python-telegram-bot[job-queue] "
            "(the APScheduler extra), otherwise monitoring never runs."
        )

    application.bot_data["state"] = state
    application.bot_data["scheduler"] = scheduler
    application.job_queue.run_once(monitor_job, when=0, name="monitoring")
    LOGGER.info("Monitoring loop armed.")

    return application

def main() -> None:
    load_dotenv()
    LOGGER.info("Starting TooGoodToMiss.")
    build_application().run_polling()

if __name__ == "__main__":
    main()
```

- [ ] **Step 13 : Vérifier que les tests de main passent**

Run : `conda run -n TooGoodToMiss python -m pytest tests/test_main.py -q`
Expected : PASS — 4 passed

- [ ] **Step 14 : Adapter les tests restants**

Dans `tests/test_utils.py` : supprimer toute classe ou méthode testant `update_lambda_env_vars`, `ok_response`, `error_response`.

Dans `tests/test_tgtg_service.py` : `TgtgService()` prend désormais un `StateStore`. Remplacer l'instanciation par :

```python
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.state = StateStore(Path(self._tmp.name) / "state.json")
        self.tgtg_service = TgtgService(self.state)

    def tearDown(self):
        self._tmp.cleanup()
```
(ajouter `from pathlib import Path`, `from tempfile import TemporaryDirectory`, `from app.core.state import StateStore`)

Dans `tests/conftest.py` : supprimer toute fixture mockant `boto3`, `dynamodb`, `events` ou `lambda`.

- [ ] **Step 15 : Vérifier qu'il ne reste aucune trace de boto3**

Run : `grep -rn "boto3\|botocore\|dynamodb\|DatabaseHandler\|update_lambda_env_vars\|lambda_arn\|core.exceptions\|ok_response\|error_response" app/ tests/`
Expected : aucune sortie.

- [ ] **Step 16 : Vérifier qu'aucun secret n'est loggé**

Run : `grep -rnE "LOGGER\.[a-z]+\(f?\".*\{.*(token|cookie|credential)" app/`
Expected : aucune sortie.

- [ ] **Step 17 : Suite complète verte**

Run : `conda run -n TooGoodToMiss python -m pytest tests/ -q`
Expected : PASS — **0 failed**. Les 2 échecs historiques ont disparu avec `tests/test_handlers.py`.

- [ ] **Step 18 : Vérifier que le bot démarre réellement**

```bash
conda run -n TooGoodToMiss pip install "python-telegram-bot[job-queue]"
conda run -n TooGoodToMiss python -m app.main
```

Expected dans les logs, dans cet ordre :
```
Starting TooGoodToMiss.
TelegramBotHandler initialized with: user_language=fr
Monitoring loop armed.
Checking favorite items and sending notifications if needed.
Next monitoring pass scheduled in ...s.
```

Vérifier ensuite que `/status` répond dans Telegram, puis arrêter avec Ctrl-C.

- [ ] **Step 19 : Vérifier que state.json n'est pas suivi par git**

Run : `git status --short`
Expected : `state.json` **absent** de la sortie.

- [ ] **Step 20 : Commit unique de l'étape**

```bash
git add -A
git commit -m "feat: le bot tourne en process unique, sans AWS

- monitoring en job PTB qui se replanifie (remplace EventBridge)
- scheduler reduit au calcul de delai, cooldown delegue au StateStore
- DynamoDB et les env vars Lambda mutables remplacees par state.json
- suppression de handlers.py, database_handler.py, telegram_service.py
- purge des 5 lignes de log qui ecrivaient des tokens en clair"
```

---

## Task 3 : Outillage de déploiement

**Files:**
- Create: `requirements.txt`, `.env.example`, `scripts/deploy.sh`, `scripts/start.sh`
- Modify: `app/common/logger.py`

**Interfaces:**
- Consumes: `app.main.main()` (Task 2)
- Produces: aucune interface Python. `scripts/start.sh` lance `python -m app.main`.

- [ ] **Step 1 : Créer requirements.txt**

```
pydantic
python-telegram-bot[job-queue]
python_dateutil
pytz
python-dotenv
requests
```

- [ ] **Step 2 : Ajouter la rotation des logs**

Remplacer `app/common/logger.py` :

```python
import logging, os
from logging.handlers import RotatingFileHandler
from pathlib import Path

LOG_DIR = Path(os.getenv("LOG_DIR", "logs"))
LOG_FILE = LOG_DIR / "app.log"
MAX_BYTES = 2 * 1024 * 1024
BACKUP_COUNT = 5

def create_logger():
    logger = logging.getLogger()
    logger.setLevel(logging.INFO)
    formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')

    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    logger.addHandler(stream_handler)

    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(LOG_FILE, maxBytes=MAX_BYTES, backupCount=BACKUP_COUNT)
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

    except OSError as e:
        logger.warning(f"File logging disabled ({e}) - falling back to stdout only.")

    return logger

LOGGER = create_logger()
```

- [ ] **Step 3 : Créer .env.example**

```bash
# Copier en .env (jamais commite).

# --- Runtime ---
USER_EMAIL=ton.email@example.com
TELEGRAM_BOT_TOKEN=123456:ABC-your-bot-token
TELEGRAM_CHAT_ID=123456789

# --- Seed initial de la session TGTG ---
# ATTENTION: ces valeurs ne servent QU'A LA CREATION de state.json.
# Les modifier ici puis redeployer n'a AUCUN effet tant que state.json
# existe sur le VPS. Pour forcer de nouveaux tokens:
#     ssh $VPS_USER@$VPS_HOST "rm $VPS_BOT_PATH/state.json"
ACCESS_TOKEN=
REFRESH_TOKEN=
TGTG_COOKIE=
LAST_TIME_TOKEN_REFRESHED=
USER_LANGUAGE=fr

# --- Deploiement (lus par scripts/deploy.sh) ---
VPS_USER=root
VPS_HOST=203.0.113.10
VPS_BOT_PATH=/root/toogoodtomiss
SSH_KEY=~/.ssh/id_ed25519
```

- [ ] **Step 4 : Créer scripts/start.sh**

```bash
#!/usr/bin/env bash
# start.sh — lance le bot en avant-plan (appele par la session tmux dans deploy.sh).

set -euo pipefail
cd "$(dirname "$0")/.."

mkdir -p logs

if [[ -f .env ]]; then
    set -a
    source .env
    set +a
fi

echo "Demarrage toogoodtomiss a $(date -u +%Y-%m-%dT%H:%M:%SZ) ..." >> logs/app.log
exec ./.venv/bin/python -m app.main >> logs/app.log 2>&1
```

- [ ] **Step 5 : Créer scripts/deploy.sh**

```bash
#!/usr/bin/env bash
# deploy.sh — synchronise le code local vers le VPS et (re)lance le bot dans tmux.
#
# Config attendue dans .env (a la racine du projet) :
#   VPS_USER, VPS_HOST                (obligatoires)
#   VPS_BOT_PATH   (defaut /root/toogoodtomiss)
#   SSH_KEY        (defaut ~/.ssh/id_ed25519)
#
# ATTENTION - state.json n'est JAMAIS pousse sur le VPS. Il porte la session
# TGTG vivante, que le bot rafraichit en continu ; l'ecraser par la copie
# locale perimee declencherait un re-login, donc un CAPTCHA.
# Corollaire: modifier ACCESS_TOKEN dans .env puis redeployer est SANS EFFET.
# Pour forcer de nouveaux tokens :
#     ssh $VPS_USER@$VPS_HOST "rm $VPS_BOT_PATH/state.json"

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
ENV_FILE="$PROJECT_DIR/.env"

if [[ -f "$ENV_FILE" ]]; then
    set -a
    source "$ENV_FILE"
    set +a
else
    echo "ERREUR : $ENV_FILE introuvable. Cree-le depuis .env.example." >&2
    exit 1
fi

: "${VPS_USER:?VPS_USER non defini dans .env}"
: "${VPS_HOST:?VPS_HOST non defini dans .env}"
: "${VPS_BOT_PATH:=/root/toogoodtomiss}"
: "${SSH_KEY:=$HOME/.ssh/id_ed25519}"
TMUX_SESSION="toogoodtomiss"

SSH_CMD="ssh -i $SSH_KEY $VPS_USER@$VPS_HOST"

echo "=== Deploiement vers $VPS_HOST:$VPS_BOT_PATH ==="

$SSH_CMD "mkdir -p $VPS_BOT_PATH"

rsync -av --delete \
    --exclude '.env' \
    --exclude 'state.json' \
    --exclude 'state.json.tmp' \
    --exclude '.venv' \
    --exclude 'logs/' \
    --exclude '.pytest_cache' \
    --exclude '__pycache__' \
    --exclude '.git' \
    -e "ssh -i $SSH_KEY" \
    "$PROJECT_DIR/" "$VPS_USER@$VPS_HOST:$VPS_BOT_PATH/"

rsync -av -e "ssh -i $SSH_KEY" "$ENV_FILE" "$VPS_USER@$VPS_HOST:$VPS_BOT_PATH/.env"

$SSH_CMD "cd $VPS_BOT_PATH && \
    { [ -d .venv ] || python3 -m venv .venv; } && \
    ./.venv/bin/pip install --quiet --upgrade pip && \
    ./.venv/bin/pip install --quiet -r requirements.txt"

$SSH_CMD "tmux kill-session -t $TMUX_SESSION 2>/dev/null || true; \
    tmux new-session -d -s $TMUX_SESSION 'cd $VPS_BOT_PATH && ./scripts/start.sh'"

echo "=== Deploiement termine ==="
echo "Logs   : $SSH_CMD 'tail -f $VPS_BOT_PATH/logs/app.log'"
echo "Session: $SSH_CMD 'tmux attach -t $TMUX_SESSION'   (detacher : Ctrl-b puis d)"
```

- [ ] **Step 6 : Rendre les scripts exécutables et valider leur syntaxe**

```bash
chmod +x scripts/deploy.sh scripts/start.sh
bash -n scripts/deploy.sh && bash -n scripts/start.sh && echo "syntaxe OK"
```
Expected : `syntaxe OK`

- [ ] **Step 7 : Vérifier que le rsync exclut bien l'état**

Run : `grep -c "exclude 'state.json'\|exclude 'state.json.tmp'\|exclude '.env'" scripts/deploy.sh`
Expected : `3`

- [ ] **Step 8 : Vérifier que la rotation des logs fonctionne**

```bash
conda run -n TooGoodToMiss python -c "
from app.common.logger import LOGGER
LOGGER.info('test rotation')
from pathlib import Path
print('logs/app.log existe:', Path('logs/app.log').exists())
"
```
Expected : `logs/app.log existe: True`

- [ ] **Step 9 : Vérifier que logs/ n'est pas suivi par git**

Run : `git status --short`
Expected : ni `logs/`, ni `state.json`.

- [ ] **Step 10 : Commit**

```bash
git add requirements.txt .env.example scripts/ app/common/logger.py
git commit -m "feat: outillage de deploiement VPS (deploy.sh, start.sh, rotation des logs)"
```

---

## Task 4 : Purge de Serverless et documentation

**Files:**
- Delete: `serverless.yaml`, `package.json`, `package-lock.json`, `lambda_layer/`, `environment.yml`
- Modify: `.github/dependabot.yml`, `.gitignore`, `README.md`, `CLAUDE.md`

- [ ] **Step 1 : Supprimer les artefacts Serverless**

```bash
git rm -r serverless.yaml package.json package-lock.json lambda_layer environment.yml
rm -rf node_modules .serverless
```

- [ ] **Step 2 : Repointer dependabot sur la racine**

Dans `.github/dependabot.yml`, remplacer `directory: "/lambda_layer"` par `directory: "/"`. Si un bloc `package-ecosystem: "npm"` existe, le supprimer — il n'y a plus de `package.json`.

- [ ] **Step 3 : Nettoyer le .gitignore**

Supprimer les entrées devenues sans objet : `.serverless/`, `lambda_layer.zip`, `python/`, `conda_packages.txt`, `node_modules`. **Conserver** `state.json`, `state.json.tmp`, `logs/`, `.env`.

- [ ] **Step 4 : Réécrire le README**

Remplacer les sections AWS / Serverless / Lambda layer par :

````markdown
## Installation

```bash
python3 -m venv .venv
./.venv/bin/pip install -r requirements.txt
cp .env.example .env   # puis renseigner les valeurs
```

## Lancer en local

```bash
./.venv/bin/python -m app.main
```

## Déployer sur un VPS

Renseigner `VPS_USER`, `VPS_HOST`, `VPS_BOT_PATH` et `SSH_KEY` dans `.env`, puis :

```bash
./scripts/deploy.sh
```

Le script synchronise le code, pousse le `.env`, installe les dépendances dans un venv distant et redémarre la session tmux `toogoodtomiss`.

```bash
ssh $VPS_USER@$VPS_HOST 'tail -f /root/toogoodtomiss/logs/app.log'   # logs
ssh $VPS_USER@$VPS_HOST 'tmux attach -t toogoodtomiss'               # session
```

## Dépannage

### Le bot utilise encore d'anciens tokens TGTG après un déploiement

C'est le comportement attendu. Les variables `ACCESS_TOKEN`, `REFRESH_TOKEN`, `TGTG_COOKIE` et `USER_LANGUAGE` du `.env` ne servent qu'**une fois**, à la création de `state.json`. Ensuite `state.json` fait autorité, et `deploy.sh` ne le pousse jamais — sinon chaque déploiement écraserait la session TGTG vivante par une copie locale périmée, ce qui déclencherait un re-login et un CAPTCHA.

Pour forcer de nouveaux tokens :

```bash
ssh $VPS_USER@$VPS_HOST "rm /root/toogoodtomiss/state.json"
./scripts/deploy.sh
```

### Le monitoring ne se déclenche jamais

Vérifier que l'extra `[job-queue]` est bien installé : sans APScheduler, `application.job_queue` vaut `None`. `app.main.build_application()` lève une `RuntimeError` explicite dans ce cas.
````

- [ ] **Step 5 : Réécrire CLAUDE.md**

Réécrire les sections décrivant l'architecture Lambda. Points obligatoires :
- Les trois Lambdas et la boucle EventBridge auto-planifiante n'existent plus : un process unique, `python -m app.main`, monitoring en job PTB qui se replanifie.
- La randomisation des délais (10–20 min le matin, 2–5 min l'après-midi, pas le dimanche) reste de l'anti-fingerprinting TGTG — ne pas la remplacer par un intervalle fixe.
- `next_delay_seconds()` ne retourne jamais `None` : c'est le seul mécanisme qui réarme le monitoring.
- `state.json` remplace DynamoDB et les env vars Lambda mutables ; écriture atomique, gitignoré, jamais poussé par `deploy.sh`.
- **Le piège du seed à sens unique** (reprendre la section Dépannage du README).
- Commande de test : `python -m pytest tests/ -q` dans le venv (la mention conda disparaît).
- Supprimer l'avertissement sur l'ARN de layer figé à `:3` et la note sur les 2 tests cassés.

- [ ] **Step 6 : Vérifier qu'il ne reste aucune trace d'AWS**

Run : `grep -rin "serverless\|lambda\|dynamodb\|eventbridge\|boto3\|aws" --exclude-dir=.git --exclude-dir=docs . | grep -v "^./README.md:.*TooGoodToGo"`
Expected : aucune sortie (hors `docs/superpowers/`, qui garde la trace historique de la migration).

- [ ] **Step 7 : Suite de tests verte**

Run : `conda run -n TooGoodToMiss python -m pytest tests/ -q`
Expected : PASS — 0 failed.

- [ ] **Step 8 : Vérifier que la mise en garde est présente aux 4 endroits exigés par la spec**

```bash
grep -l "state.json" scripts/deploy.sh README.md CLAUDE.md app/core/state.py
```
Expected : les 4 fichiers listés.

- [ ] **Step 9 : Commit**

```bash
git add -A
git commit -m "chore: purge de Serverless/AWS et reecriture de la documentation VPS"
```

---

## Après le merge — checklist de bascule

Ces étapes sont **manuelles** et interviennent après validation en réel. Ne pas les exécuter pendant l'implémentation.

- [ ] `./scripts/deploy.sh` vers le VPS, puis observer un cycle de monitoring complet dans `logs/app.log`.
- [ ] Vérifier qu'une notification arrive bien sur Telegram et que `/pause` puis `/wakeup` fonctionnent.
- [ ] `serverless remove` sur l'ancienne stack AWS — sinon elle continue de tourner et de facturer (dont la table DynamoDB provisionnée 10/10). **Attention** : `serverless.yaml` ayant été supprimé, cette commande doit être lancée depuis le commit `8af3fb2` (`git stash` / `git worktree add`), ou la stack supprimée à la main dans la console CloudFormation.
- [ ] Révoquer le token Telegram via @BotFather et réinitialiser la session TGTG — les zips déjà déployés sur S3 contiennent le `.env`.
- [ ] Passer la tâche Notion [Supprimer serverless/AWS Lambda](https://app.notion.com/p/3a5ebd78e2ee81508ce1ec76db5acbbc) en `Done`.
