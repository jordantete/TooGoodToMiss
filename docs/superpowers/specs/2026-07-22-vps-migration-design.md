# Migration AWS Lambda → VPS

**Date** : 2026-07-22
**Tâche Notion** : [Supprimer serverless/AWS Lambda et passer à un deploy.sh VPS](https://app.notion.com/p/3a5ebd78e2ee81508ce1ec76db5acbbc)
**Branche** : `feat/vps-migration`

## Objectif

Supprimer toute trace de Serverless Framework et d'AWS (code, config, doc), et rendre le bot déployable sur n'importe quel VPS via un `deploy.sh`, sur le modèle de `history_whisper_bot`.

## Contrainte transverse : aucun secret dans l'historique git

Audit réalisé avant conception, sur l'intégralité de l'historique :

- `.env` est dans `.gitignore` et n'apparaît dans aucun commit.
- Scan de tous les blobs de tous les commits (`AKIA[0-9A-Z]{16}`, `[0-9]{9,10}:AA[\w-]{33}`, `eyJ[\w-]{20,}`, `datadome=[\w]{20,}`) : zéro résultat.
- `serverless.yaml` n'a jamais contenu que des `${env:...}`.

**Rien à purger rétroactivement.** Le risque est créé par la migration elle-même et se traite en Section 3.

## 1. Architecture cible

Process unique, lancé par `python -m app.main`, maintenu par tmux sur le VPS.

```
NOUVEAU   app/main.py                          charge .env → Application PTB → handlers + job → run_polling()
NOUVEAU   app/core/state.py                    StateStore : state.json, écriture atomique
MODIFIÉ   app/core/scheduler.py                214 → ~60 lignes (tout EventBridge saute)
MODIFIÉ   app/core/telegram_bot_handler.py     plus d'initialize/shutdown par requête
MODIFIÉ   app/common/utils.py                  −update_lambda_env_vars, −ok_response, −error_response
MODIFIÉ   app/common/logger.py                 RotatingFileHandler
MODIFIÉ   app/services/tgtg_service_monitor.py env vars → StateStore
MODIFIÉ   app/services/tgtg_service/tgtg_service.py   purge des logs de credentials
SUPPRIMÉ  app/handlers.py                      les 3 handlers Lambda
SUPPRIMÉ  app/core/database_handler.py         DynamoDB, absorbé par StateStore
```

### Modèle de process

Un seul loop asyncio, piloté par `run_polling()`. Le monitoring est un job PTB qui **se replanifie lui-même**, ce qui préserve à l'identique la nature auto-planifiante actuelle : on remplace la règle EventBridge one-shot par un `run_once`, rien d'autre.

```
run_polling()
 ├─ handlers Telegram  (/start /pause /wakeup /status …)
 └─ JobQueue → monitor_job
       ├─ state.is_paused() ?           → replanifie, ne fait rien
       ├─ hors fenêtre / dimanche ?     → replanifie
       ├─ TgtgServiceMonitor.monitor()
       │    ├─ tokens rafraîchis        → state.save_tgtg_credentials()
       │    ├─ captcha (ForbiddenError) → state.set_cooldown(30 min)
       │    └─ items > 0 et pas notifié aujourd'hui → notif + state.mark_notified()
       └─ run_once(monitor_job, delay=next_delay())
```

**La randomisation est conservée telle quelle** : 10–20 min sur la fenêtre du matin (10h–12h), 2–5 min l'après-midi (12h–19h), pas d'exécution le dimanche. C'est de l'anti-fingerprinting TGTG, pas du confort — ne pas remplacer par un intervalle fixe.

`_calculate_next_invocation_time` est déjà de la logique pure (fenêtres, `random.randint`, skip dimanche). Elle est **extraite**, pas réécrite. Ce qui disparaît de `scheduler.py` est l'enrobage EventBridge : `_list_scheduled_rules`, `_is_future_rule`, `_extract_datetime_from_rule`, `_delete_past_rule`, `_has_future_invocation`, `_create_rule`, `_convert_datetime_to_cron_expression`.

### Dépendance à ne pas manquer

`application.job_queue` vaut `None` si APScheduler est absent — c'est le cas aujourd'hui dans l'env conda. Le `requirements.txt` doit spécifier `python-telegram-bot[job-queue]`, pas `python-telegram-bot`.

## 2. StateStore

Une classe, un fichier, une responsabilité. Remplace à la fois DynamoDB et les env vars Lambda mutables.

```python
class StateStore:
    def __init__(self, path: Path)          # crée state.json en 0600 si absent
    # session TGTG
    def get_tgtg_credentials(self)          # seed depuis .env au 1er appel si vide
    def save_tgtg_credentials(self, ...)
    # pause / cooldown
    def is_paused(self) -> bool
    def cooldown_remaining(self) -> float | None
    def set_cooldown(self, minutes: int)
    def clear_cooldown(self)
    # préférences
    def get_language(self)
    def set_language(self, lang)
    # déduplication des notifications
    def was_notified_today(self, store_id) -> bool
    def mark_notified(self, store_id)
```

Forme du fichier :

```json
{
  "tgtg": {
    "access_token": "...",
    "refresh_token": "...",
    "cookie": "...",
    "last_refreshed": "2026-07-22T10:00:00Z"
  },
  "cooldown_end_time": null,
  "user_language": "fr",
  "notifications": { "store-4821": "2026-07-22" }
}
```

**Décisions :**

- **Écriture atomique** : `json.dump` → `state.json.tmp` → `os.replace`. Le process écrit à chaque refresh de token ; une coupure ne doit pas laisser un JSON tronqué qui rendrait le bot non démarrable.
- **Purge à l'écriture** : les entrées `notifications` antérieures au jour courant (UTC) sont supprimées, ce qui borne la taille du fichier sans job de nettoyage.
- **`chmod 0600`** à la création.
- **Bootstrap** : si `state.json` est absent au démarrage, il est initialisé depuis `ACCESS_TOKEN` / `REFRESH_TOKEN` / `TGTG_COOKIE` / `LAST_TIME_TOKEN_REFRESHED` **et `USER_LANGUAGE`** du `.env`. Ensuite `state.json` fait autorité et ces variables ne sont plus jamais relues. Si les tokens sont vides ou morts, le flow login-by-email du client TGTG vendoré sert de secours.

### ⚠️ Le piège du seed à sens unique

Le seed depuis `.env` **ne joue qu'une fois**, à la création de `state.json`. Ensuite le `.env` est ignoré pour tout ce qui est état.

Conséquence : **modifier `ACCESS_TOKEN` dans le `.env` puis redéployer n'a aucun effet.** `deploy.sh` pousse bien le nouveau `.env`, mais le bot continue de lire `state.json`. Pour forcer de nouveaux tokens, il faut supprimer `state.json` sur le VPS :

```bash
ssh $VPS_USER@$VPS_HOST "rm $VPS_BOT_PATH/state.json"
# puis redéployer, ou juste redémarrer la session tmux
```

C'est le prix à payer pour que `deploy.sh` n'écrase jamais une session TGTG fraîche. Le comportement est volontaire, mais contre-intuitif à froid — et typiquement le genre de chose qu'on ne retrouve pas quand le bot est muet à 3h du matin.

**Cette mise en garde doit être répétée à quatre endroits** (à traiter en étapes 3 et 4, et vérifiable) :

1. l'en-tête de commentaire de `scripts/deploy.sh` ;
2. une section « Dépannage » du `README.md` ;
3. `CLAUDE.md`, dans la description de la couche d'état ;
4. la docstring de `StateStore.get_tgtg_credentials`.

## 3. Secrets et déploiement

### Recomposition du `.env`

| Clé | Devient |
|---|---|
| `USER_EMAIL`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` | restent — vrais secrets runtime |
| `ACCESS_TOKEN`, `REFRESH_TOKEN`, `TGTG_COOKIE`, `LAST_TIME_TOKEN_REFRESHED` | restent, en **seed initial uniquement** |
| `USER_LANGUAGE` | reste, en **seed initial uniquement** ; `state.json` fait autorité après le 1er démarrage |
| `COOLDOWN_END_TIME` | **part** dans `state.json` (état pur, jamais seedé) |
| `AWS_ACCOUNT_ID`, `DEFAULT_AWS_REGION` | **supprimées** |
| `VPS_USER`, `VPS_HOST`, `VPS_BOT_PATH`, `SSH_KEY` | **ajoutées**, lues par `deploy.sh` |

`.env.example` ne contient que des placeholders (`123456:ABC-your-bot-token`, `203.0.113.10`), calqué sur `history_whisper_bot`.

### Les quatre garde-fous

1. **`.gitignore` en premier, au commit 1** : `state.json`, `state.json.tmp`, `logs/`. Avant que la moindre ligne de code ne les écrive — l'ordre compte, pas l'intention.

2. **Purge des logs de credentials.** Sept lignes écrivent aujourd'hui des tokens en clair :

   | Ligne | Contenu | Sort |
   |---|---|---|
   | `app/common/utils.py:110` | `new_env_vars` | disparaît avec `update_lambda_env_vars` |
   | `app/common/utils.py:114` | `current_env_vars`, donc les 3 tokens | disparaît avec `update_lambda_env_vars` |
   | `app/services/tgtg_service_monitor.py:27` | access/refresh token, cookie | à purger |
   | `app/services/tgtg_service_monitor.py:94` | `new_credentials` | à purger |
   | `app/services/tgtg_service/tgtg_service.py:42` | email + 3 tokens, à chaque login | à purger |
   | `app/services/tgtg_service/tgtg_service.py:55` | 3 tokens + `last_time_token_refreshed` | à purger |
   | `app/services/tgtg_service/tgtg_service.py:64` | `self.credentials`, à chaque requête | à purger |

   Deux disparaissent gratuitement avec `update_lambda_env_vars`. Les **cinq** restantes perdent leurs valeurs et gardent l'événement (`LOGGER.info("TGTG credentials refreshed")`). Pas de helper de masquage : on ne loggue plus la valeur, point. Sur Lambda ces logs partaient dans CloudWatch ; sur le VPS `start.sh` redirige tout vers `logs/app.log`, qui deviendrait sinon un fichier de secrets en clair, permanent et non tournant.

3. **Rotation des logs** via `RotatingFileHandler` dans `app/common/logger.py` (5 × 2 Mo). Rien à configurer sur le VPS.

4. **`rsync` ne touche jamais à l'état du VPS** : `--exclude .env --exclude state.json --exclude 'state.json.tmp' --exclude logs/ --exclude .venv --exclude .git --exclude __pycache__`. Le `.env` est poussé dans une passe séparée. `state.json` n'est **jamais** poussé : chaque déploiement écraserait sinon la session TGTG fraîche du VPS par la copie locale périmée, déclenchant un re-login et donc un captcha.

### Scripts

`scripts/deploy.sh` reprend le squelette de `history_whisper_bot/scripts/deploy.sh` : source `.env` → `mkdir -p` cible → `rsync` du code → `rsync` du `.env` → création/maj du venv → `pip install -r requirements.txt` → `tmux kill-session` puis `new-session`. Session nommée `toogoodtomiss`.

`scripts/start.sh` : `mkdir -p logs`, source `.env`, `exec ./.venv/bin/python -m app.main >> logs/app.log 2>&1`.

## 4. Tests

| Fichier | Sort |
|---|---|
| `tests/test_state.py` | **nouveau** — atomicité (coupure en cours d'écriture), purge des notifs périmées, dédup, seed depuis `.env`, permissions 0600 |
| `tests/test_scheduler.py` | refonte — le calcul de délai devient pur, testable sous `freezegun` sans aucun mock |
| `tests/test_database_handler.py` | supprimé, absorbé par `test_state.py` |
| `tests/test_handlers.py` | supprimé → `tests/test_main.py`, couvrant le job de monitoring et sa replanification |
| `tests/test_utils.py` | retrait des cas `update_lambda_env_vars` |
| `tests/conftest.py` | les fixtures boto3 disparaissent |

Les 2 tests actuellement cassés sur `main` ne seront pas *réparés* : ils **disparaissent avec leur cause**. Ils échouent sur `botocore … You must specify a region`, c'est-à-dire sur l'initialisation d'un client AWS. Plus de boto3, plus d'échec. La suite doit être à 100 % verte à la fin de l'étape 2.

## 5. Séquencement

Branche `feat/vps-migration`. Le Lambda reste en production tant que la branche n'est pas mergée et validée.

1. **`state.json` + dédup** — `.gitignore` d'abord, puis `StateStore` + `test_state.py`. Aucun autre module touché, suite verte. Rien ne consomme encore le store.
2. **Cœur applicatif** — `main.py`, JobQueue, scheduler réduit, purge de boto3 / `handlers.py` / `database_handler.py`, purge des logs de credentials, refonte des tests. *Critère de sortie : `python -m app.main` tourne en local et notifie réellement.*
3. **Outillage de déploiement** — `requirements.txt`, `.env.example`, `scripts/deploy.sh`, `scripts/start.sh`, rotation des logs.
4. **Purge et doc** — `serverless.yaml`, `package.json`, `package-lock.json`, `lambda_layer/`, `environment.yml`, repointage de `.github/dependabot.yml` sur `/`, réécriture des sections AWS de `README.md` et `CLAUDE.md`.

### Après le merge

Le point de non-retour se situe après le merge, pas pendant :

1. Premier `deploy.sh` sur le VPS, observation d'un cycle de monitoring complet.
2. `serverless remove` pour purger la stack AWS — sinon elle continue de tourner et de facturer (dont la table DynamoDB provisionnée 10/10).
3. Révocation des tokens embarqués dans les zips déjà déployés : Telegram via @BotFather, et réinitialisation de la session TGTG.

## Bénéfices attendus

- Supprime la fuite du `.env` dans les zips de déploiement.
- Supprime la surface d'attaque du webhook non authentifié (le polling n'expose aucun endpoint).
- Supprime l'ARN de layer figé à `:3` et la divergence de nom de stack CloudFormation héritée du renommage.
- Coût AWS → 0.

## Hors périmètre

- Migration vers `uv` / `pyproject.toml` : `history_whisper_bot`, le modèle de référence, utilise `requirements.txt` et son `deploy.sh` fait `pip install -r requirements.txt`. On s'aligne.
- Toute évolution fonctionnelle du bot (réservation automatique, nouvelles commandes Telegram).
- Le renommage des ressources AWS : elles sont détruites en fin de migration, pas renommées.
