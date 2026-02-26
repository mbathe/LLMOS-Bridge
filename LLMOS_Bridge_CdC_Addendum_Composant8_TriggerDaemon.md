# LLMOS Bridge — Cahier des Charges
## Addendum : Composant 8 — TriggerDaemon & Système d'Événements Universel

**Version :** 1.0
**Date :** 2026-02-26
**Statut :** Implémenté — Sprint 1.6
**Complète :** LLMOS_Bridge_CdC_v2_Complet (Composants 1–7)

---

## Table des matières

1. [Contexte et motivation](#1-contexte-et-motivation)
2. [Vision architecturale](#2-vision-architecturale)
3. [Composant 8A — Système d'Événements Universel](#3-composant-8a--système-dévénements-universel)
4. [Composant 8B — TriggerDaemon](#4-composant-8b--triggerdaemon)
5. [Types de déclencheurs](#5-types-de-déclencheurs)
6. [Planificateur prioritaire & résolution de conflits](#6-planificateur-prioritaire--résolution-de-conflits)
7. [Persistance des triggers](#7-persistance-des-triggers)
8. [Module IML : triggers](#8-module-iml--triggers)
9. [API REST](#9-api-rest)
10. [Intégration avec l'architecture existante](#10-intégration-avec-larchitecture-existante)
11. [Sécurité](#11-sécurité)
12. [Configuration](#12-configuration)
13. [Tests et couverture](#13-tests-et-couverture)
14. [Évolutions futures](#14-évolutions-futures)

---

## 1. Contexte et motivation

### 1.1 Problème résolu

Les Composants 1–7 de LLMOS Bridge permettent à un LLM de soumettre des plans IML et d'exécuter des actions sur le système d'exploitation de manière **réactive** — le LLM reçoit une requête, construit un plan, le soumet.

Cependant, de nombreux cas d'usage nécessitent une **automation proactive** :

| Cas d'usage | Déclencheur | Action LLM |
|---|---|---|
| Backup nocturne | Cron 02:00 | Compresse et archive les fichiers modifiés |
| Alerte CPU | CPU > 90% pendant 30s | Identifie le processus fautif, génère un rapport |
| Pipeline CI | Nouveau fichier dans `/builds/` | Lance les tests, envoie les résultats |
| Surveillance process | `nginx` s'arrête | Redémarre le service, alerte l'administrateur |
| Chaînage LLM | Plan A se termine | Crée le trigger B pour la phase suivante |

Ces scénarios nécessitent un composant autonome, persistant, qui surveille l'environnement et soumet des plans automatiquement — sans intervention humaine à chaque déclenchement.

### 1.2 Analogie système

TriggerDaemon est le **systemd de LLMOS Bridge** : il gère des unités réactives (triggers) dont chacune peut démarrer, s'arrêter, échouer, être relancée, et déclencher d'autres unités en cascade.

| Concept systemd | Équivalent TriggerDaemon |
|---|---|
| Service unit (.service) | TriggerDefinition |
| Timer unit (.timer) | TemporalTrigger |
| PathUnit (.path) | FileSystemTrigger |
| Wants=/Requires= | Composite AND/SEQ |
| RestartPolicy | conflict_policy + health monitoring |
| journald | TriggerHealth + EventBus |

---

## 2. Vision architecturale

### 2.1 Diagramme de flux principal

```
┌─────────────────────────────────────────────────────────┐
│                      TriggerDaemon                       │
│                                                         │
│  ┌──────────────┐   fire_callback   ┌───────────────┐  │
│  │  BaseWatcher │ ──────────────→   │ _on_watcher_  │  │
│  │  (asyncio    │                   │    fire()     │  │
│  │   task)      │                   └───────┬───────┘  │
│  └──────────────┘                           │           │
│                                    can_fire()? throttle?│
│                                             │           │
│                                    ┌────────▼──────────┐│
│                                    │PriorityFire        ││
│                                    │Scheduler.enqueue() ││
│                                    └────────┬──────────┘│
│                                             │           │
│                                    ConflictResolver      │
│                                    resource lock?        │
│                                             │           │
│                                    ┌────────▼──────────┐│
│                                    │  _submit_plan()    ││
│                                    │  PlanExecutor      ││
│                                    └────────┬──────────┘│
│                                             │           │
└─────────────────────────────────────────────┼───────────┘
                                              │
                                    EventBus.emit()
                                    "llmos.triggers"
```

### 2.2 Composants implémentés

```
llmos_bridge/
├── events/                   ← Composant 8A (Événements Universels)
│   ├── __init__.py
│   ├── bus.py                ← Existant (Phase 1.5)
│   ├── models.py             ← NOUVEAU: UniversalEvent, EventPriority
│   ├── router.py             ← NOUVEAU: EventRouter, topic_matches()
│   └── session.py            ← NOUVEAU: SessionContextPropagator
│
└── triggers/                 ← Composant 8B (TriggerDaemon)
    ├── __init__.py
    ├── models.py             ← TriggerDefinition + types
    ├── store.py              ← Persistance SQLite
    ├── scheduler.py          ← PriorityFireScheduler
    ├── conflict.py           ← ConflictResolver
    ├── daemon.py             ← TriggerDaemon (orchestrateur)
    └── watchers/
        ├── __init__.py
        ├── base.py           ← BaseWatcher ABC + WatcherFactory
        ├── temporal.py       ← CronWatcher, IntervalWatcher, OnceWatcher
        ├── system.py         ← FileSystemWatcher, ProcessWatcher, ResourceWatcher
        └── composite.py      ← CompositeWatcher (AND/OR/NOT/SEQ/WINDOW)
```

---

## 3. Composant 8A — Système d'Événements Universel

### 3.1 Motivation

L'EventBus existant (`events/bus.py`) transporte des `dict` non typés. Pour la traçabilité des triggers, il faut :
- Relier les événements entre eux (chaîne de causalité)
- Attribuer chaque événement à une session LLM
- Grouper les événements d'une même opération (correlation_id)
- Router sélectivement par motif de topic (MQTT-style)

### 3.2 UniversalEvent

Enveloppe typée, **opt-in** : tout code existant émettant des `dict` bruts reste inchangé.

**Champs clés :**

| Champ | Type | Description |
|---|---|---|
| `id` | `str` (UUID4) | Identifiant unique de l'événement |
| `type` | `str` | Type sémantique : `"trigger.fired"`, `"plan.submitted"` |
| `topic` | `str` | Topic du bus : `"llmos.triggers"`, `"llmos.plans"` |
| `timestamp` | `float` | Unix timestamp de création |
| `source` | `str` | Composant émetteur : `"trigger_daemon"`, `"executor"` |
| `payload` | `dict` | Données spécifiques à l'événement |
| `caused_by` | `str?` | ID de l'événement parent (causalité) |
| `causes` | `list[str]` | IDs des événements enfants (peuplé lazily) |
| `session_id` | `str?` | Session LLM d'origine |
| `correlation_id` | `str?` | Regroupe les événements d'une opération |
| `priority` | `EventPriority` | Priorité de traitement (advisory) |
| `metadata` | `dict` | Métadonnées extensibles |

**Méthodes :**
- `to_dict()` → compatible `EventBus.emit()`
- `from_dict(d)` → reconstruction depuis un dict bus
- `spawn_child(type, topic, source, payload)` → crée un événement enfant lié par causalité

**EventPriority (IntEnum) :**
```
CRITICAL=0, HIGH=1, NORMAL=2, LOW=3, BACKGROUND=4
```

### 3.3 EventRouter

`EventBus` étendu avec routage par motif MQTT :

**Wildcards supportés :**

| Motif | Signification | Exemple |
|---|---|---|
| `*` | Un segment quelconque | `llmos.*.started` matche `llmos.plan.started` |
| `#` | Zéro ou plusieurs segments | `llmos.triggers.#` matche `llmos.triggers` ET `llmos.triggers.fire.critical` |

**Fonction `topic_matches(pattern, topic)` :**
- Normalise `/` → `.` pour compatibilité MQTT
- Convertit `*` → regex `[^.]+`
- Convertit `#` en fin de segment → `(\..+)?` (zero or more sub-segments)
- Retourne un `bool`

**Usage :**
```python
router = EventRouter(fallback=NullEventBus())
router.add_route("llmos.triggers.*", trigger_monitor_handler)
router.add_route("llmos.#", global_audit_handler)
await router.emit("llmos.triggers.fired", {"trigger_id": "t1"})
```

### 3.4 SessionContextPropagator

Lie un `plan_id` à son contexte de trigger (variables template) :

```python
propagator = SessionContextPropagator()

# À la soumission du plan
await propagator.bind(plan_id, {
    "trigger_id": "t1",
    "trigger_name": "backup_nightly",
    "event_type": "temporal.cron",
    "payload": {},
    "fired_at": 1706789012.4,
})

# Dans l'executor (synchrone)
ctx = propagator.get(plan_id)  # dict | None

# Après complétion du plan
await propagator.unbind(plan_id)
```

---

## 4. Composant 8B — TriggerDaemon

### 4.1 Cycle de vie

**Démarrage (startup FastAPI) :**

```
TriggerStore.init()          ← Ouvre SQLite, crée tables
TriggerStore.load_active()   ← Charge tous les triggers ACTIVE/WATCHING
  ↓
TriggerDaemon.start()
  ├── PriorityFireScheduler.start()
  ├── _arm(trigger) × N      ← Crée un BaseWatcher par trigger actif
  └── asyncio.create_task(_health_loop())
```

**Arrêt (shutdown FastAPI) :**
```
TriggerDaemon.stop()
  ├── _health_task.cancel()
  ├── asyncio.gather(*[w.stop() for w in watchers])
  └── PriorityFireScheduler.stop()
```

### 4.2 Machine à états d'un trigger

```
                    [register(enabled=False)]
                           ↓
   [register]         INACTIVE ←─────────────────────────────┐
       ↓                  ↑ deactivate()                      │
   REGISTERED         activate()                              │
       ↓                  │                                   │
   [register(enabled=True)]                                   │
       ↓                                                      │
     ACTIVE ──── watcher fires ──→ FIRED ──→ ACTIVE (re-arm) │
       │                               │                      │
       │             throttled         │                      │
       └──────────→ THROTTLED ─────────┘                      │
       │                                                      │
       └──── watcher error ──→ FAILED ──────────────────────→─┘
                                         (manual re-enable)
       ACTIVE/WATCHING (composites partiels)
```

### 4.3 API programmatique (TriggerDaemon)

```python
# Enregistrement (crée + arme si enabled=True)
trigger = await daemon.register(trigger_definition)

# Gestion du cycle de vie
await daemon.activate(trigger_id)     # arme le trigger
await daemon.deactivate(trigger_id)   # désarme sans supprimer
deleted = await daemon.delete(trigger_id)  # désarme + supprime

# Consultation
trigger = await daemon.get(trigger_id)
all_triggers = await daemon.list_all()
active_triggers = await daemon.list_active()
```

---

## 5. Types de déclencheurs

### 5.1 TEMPORAL — `watchers/temporal.py`

Trois sous-types selon les paramètres fournis :

#### IntervalWatcher
```json
{
  "type": "temporal",
  "params": { "interval_seconds": 300 }
}
```
Implémentation : `asyncio.wait_for(stop_event.wait(), timeout=interval_seconds)` — pas de drift.

#### CronWatcher
```json
{
  "type": "temporal",
  "params": { "schedule": "0 9 * * 1-5" }
}
```
Implémentation : `croniter` (dépendance optionnelle) — calcule le prochain déclenchement exact.

#### OnceWatcher
```json
{
  "type": "temporal",
  "params": { "run_at": 1706789012.4 }
}
```
Implémentation : Se déclenche une fois au timestamp indiqué, puis s'arrête (terminal).

### 5.2 FILESYSTEM — `watchers/system.py`

```json
{
  "type": "filesystem",
  "params": {
    "path": "/home/user/inbox",
    "recursive": false,
    "events": ["created", "modified"]
  }
}
```
Implémentation : `watchfiles.awatch()` (dépendance optionnelle). Mappe `Change.added/modified/deleted` aux types d'événements IML.

### 5.3 PROCESS — `watchers/system.py`

```json
{
  "type": "process",
  "params": {
    "name": "nginx",
    "event": "stopped"
  }
}
```
Implémentation : Poll `psutil.process_iter()` toutes les `poll_interval_seconds` (défaut : 5s). Compare l'ensemble des PIDs courants à l'ensemble précédent.

### 5.4 RESOURCE — `watchers/system.py`

```json
{
  "type": "resource",
  "params": {
    "metric": "cpu_percent",
    "threshold": 90.0,
    "duration_seconds": 30.0
  }
}
```
Métriques supportées : `cpu_percent`, `memory_percent`, `disk_percent` (via `psutil`).
Le seuil doit être maintenu pendant `duration_seconds` consécutives avant déclenchement.

### 5.5 COMPOSITE — `watchers/composite.py`

Combine plusieurs triggers existants avec un opérateur logique.

#### Opérateur AND
```json
{
  "operator": "AND",
  "trigger_ids": ["t_file_changed", "t_process_running"],
  "timeout_seconds": 60
}
```
Se déclenche quand **tous** les sous-triggers ont tiré dans la fenêtre de timeout.

#### Opérateur OR
```json
{
  "operator": "OR",
  "trigger_ids": ["t_alert_cpu", "t_alert_memory"]
}
```
Se déclenche quand **l'un quelconque** des sous-triggers tire.

#### Opérateur NOT
```json
{
  "operator": "NOT",
  "trigger_ids": ["t_business_hours"],
  "silence_seconds": 300
}
```
Se déclenche quand tous les sous-triggers restent **silencieux** pendant `silence_seconds`.

#### Opérateur SEQ
```json
{
  "operator": "SEQ",
  "trigger_ids": ["t_deploy_started", "t_tests_passed", "t_qa_approved"],
  "timeout_seconds": 3600
}
```
Se déclenche quand les sous-triggers tirent **dans l'ordre exact** dans le timeout.

#### Opérateur WINDOW
```json
{
  "operator": "WINDOW",
  "trigger_ids": ["t_error_log"],
  "count": 5,
  "window_seconds": 60
}
```
Se déclenche quand un sous-trigger tire **N fois** dans une fenêtre glissante de `window_seconds`.

---

## 6. Planificateur prioritaire & résolution de conflits

### 6.1 PriorityFireScheduler (`scheduler.py`)

File d'attente min-heap ordonnée par `TriggerPriority` (inversé : CRITICAL=0 → plus haute urgence).

**Fonctionnalités :**
- **Rate limiting** : `max_fires_per_hour` par trigger (fenêtre glissante d'une heure)
- **Contrôle de concurrence** : `max_concurrent_plans` plans simultanés (défaut : 5)
- **Préemption** : un trigger CRITICAL peut interrompre un plan BACKGROUND en cours
- **Politique reject** : si le même trigger a déjà un plan en cours, le nouveau tir est ignoré

**Paramètres :**
```python
PriorityFireScheduler(
    submit_callback=daemon._submit_plan,
    cancel_callback=daemon._cancel_plan,
    max_concurrent=5,
)
```

### 6.2 ConflictResolver (`conflict.py`)

Table de verrous en mémoire : `resource_name → plan_id`.

**Politiques de conflit :**

| Politique | Comportement |
|---|---|
| `queue` (défaut) | Attend que la ressource soit libérée (timeout 60s) |
| `preempt` | Annule le plan courant et lance le nouveau |
| `reject` | Abandonne le tir si la ressource est verrouillée |

**Configuration par trigger :**
```json
{
  "resource_lock": "backup_storage",
  "conflict_policy": "queue"
}
```

Deux triggers avec le même `resource_lock` ne peuvent jamais avoir des plans actifs simultanément.

---

## 7. Persistance des triggers

### 7.1 TriggerStore (`store.py`)

**Base de données :** SQLite via `aiosqlite` — même pattern que `orchestration/state.py`.

**Schéma :**
```sql
CREATE TABLE triggers (
    trigger_id  TEXT PRIMARY KEY,
    name        TEXT NOT NULL DEFAULT '',
    state       TEXT NOT NULL DEFAULT 'registered',
    enabled     INTEGER NOT NULL DEFAULT 1,
    definition  TEXT NOT NULL,          -- JSON complet du TriggerDefinition
    created_at  REAL NOT NULL,
    updated_at  REAL NOT NULL,
    expires_at  REAL                    -- NULL = permanent
);
CREATE INDEX idx_triggers_state   ON triggers(state);
CREATE INDEX idx_triggers_enabled ON triggers(enabled);
```

**Invariant critique :** La colonne `state` est **autoritaire** — `update_state()` met à jour uniquement la colonne (fast-path). Toutes les lectures (`get()`, `list_all()`, etc.) fetchen les deux colonnes et écrasent l'état du JSON avec la valeur de la colonne.

**Opérations :**
```python
await store.init()                           # Ouvre + migrate
await store.save(trigger)                    # INSERT OR UPDATE
trigger = await store.get(trigger_id)        # None si non trouvé
triggers = await store.list_all()            # Tous états
triggers = await store.load_active()         # ACTIVE ou WATCHING, enabled=1
triggers = await store.list_by_state(state)  # Filtre par état
await store.update_state(trigger_id, state)  # Fast-path (colonne uniquement)
deleted = await store.delete(trigger_id)     # True si existait
count = await store.purge_expired()          # Supprime les triggers expirés
```

### 7.2 Persistance à travers les redémarrages

Au démarrage du daemon :
```python
active_triggers = await store.load_active()
for trigger in active_triggers:
    self._triggers[trigger.trigger_id] = trigger
    await self._arm(trigger)  # Recréé le watcher
```

Tous les triggers ACTIVE ou WATCHING au moment du shutdown sont automatiquement réarmés au redémarrage suivant.

### 7.3 Expiration automatique

Chaque trigger peut avoir un `expires_at` (Unix timestamp). La boucle de santé (`_health_loop`) appelle `store.purge_expired()` toutes les 30 secondes.

---

## 8. Module IML : triggers

### 8.1 TriggerModule (`modules/triggers/module.py`)

**MODULE_ID :** `"triggers"`
**VERSION :** `"1.0.0"`
**PLATFORMS :** `["linux", "darwin", "windows"]`

Fournit au LLM l'accès programmatique au TriggerDaemon. Le daemon est injecté via `set_daemon()` au démarrage du serveur (pas de dépendance circulaire).

### 8.2 Actions disponibles

| Action | Permission | Description |
|---|---|---|
| `register_trigger` | `power_user` | Enregistre et arme un nouveau trigger |
| `activate_trigger` | `power_user` | Réarme un trigger existant |
| `deactivate_trigger` | `power_user` | Désarme sans supprimer |
| `delete_trigger` | `power_user` | Supprime définitivement |
| `list_triggers` | `local_worker` | Liste avec filtres état/type/tags/created_by |
| `get_trigger` | `local_worker` | Récupère un trigger avec métriques de santé |

### 8.3 Exemple IML — Trigger de backup horaire

```json
{
  "plan_id": "setup_backup",
  "protocol_version": "2.0",
  "description": "Configure un backup automatique toutes les heures",
  "actions": [
    {
      "id": "create_backup_trigger",
      "action": "register_trigger",
      "module": "triggers",
      "params": {
        "name": "hourly_backup",
        "description": "Sauvegarde automatique toutes les heures",
        "condition": {
          "type": "temporal",
          "params": { "interval_seconds": 3600 }
        },
        "plan_template": {
          "protocol_version": "2.0",
          "description": "Backup automatique",
          "actions": [
            {
              "id": "compress",
              "action": "run_command",
              "module": "os_exec",
              "params": {
                "command": ["tar", "-czf", "/backup/{{trigger.fired_at}}.tar.gz", "/home/user/data"]
              }
            }
          ]
        },
        "priority": "normal",
        "conflict_policy": "reject",
        "resource_lock": "backup_storage",
        "tags": ["backup", "scheduled"]
      }
    }
  ]
}
```

### 8.4 Chaînage de triggers

Un plan lancé par un trigger peut créer de nouveaux triggers (chaînage dynamique) :

```json
{
  "action": "register_trigger",
  "module": "triggers",
  "params": {
    "name": "post_deploy_tests",
    "_chain_depth": 1,
    "condition": {
      "type": "filesystem",
      "params": { "path": "/deploys/complete", "events": ["created"] }
    },
    "plan_template": { ... }
  }
}
```

**Protection contre les boucles infinies :**
- Chaque trigger a `chain_depth` (profondeur actuelle) et `max_chain_depth` (défaut : 5)
- TriggerDaemon rejette `register()` si `chain_depth > max_chain_depth`
- Les triggers créés par des plans héritent automatiquement de `chain_depth + 1`

---

## 9. API REST

**Préfixe :** `/triggers`
**Condition préalable :** `triggers.enabled = true` dans la configuration

### 9.1 Endpoints

| Méthode | Chemin | Description | Statut |
|---|---|---|---|
| `GET` | `/triggers` | Liste tous les triggers | 200 |
| `POST` | `/triggers` | Enregistre un nouveau trigger | 201 |
| `GET` | `/triggers/{id}` | Détails + métriques de santé | 200 |
| `PUT` | `/triggers/{id}/activate` | Arme un trigger | 200 |
| `PUT` | `/triggers/{id}/deactivate` | Désarme un trigger | 200 |
| `DELETE` | `/triggers/{id}` | Supprime définitivement | 204 |

**Erreur si daemon désactivé :** `503 Service Unavailable`
**Ressource non trouvée :** `404 Not Found`

### 9.2 Exemple — Enregistrement via API

```http
POST /triggers
Content-Type: application/json

{
  "name": "cpu_alert",
  "condition": {
    "type": "resource",
    "params": {
      "metric": "cpu_percent",
      "threshold": 85.0,
      "duration_seconds": 60
    }
  },
  "plan_template": {
    "protocol_version": "2.0",
    "actions": [
      {
        "id": "run_top",
        "action": "run_command",
        "module": "os_exec",
        "params": { "command": ["ps", "aux", "--sort=-%cpu"] }
      }
    ]
  },
  "priority": "high",
  "max_fires_per_hour": 4,
  "tags": ["monitoring", "cpu"]
}
```

**Réponse :**
```json
{
  "trigger_id": "a3f9b2e1-...",
  "name": "cpu_alert",
  "state": "active",
  "message": "Trigger registered successfully"
}
```

### 9.3 Réponse GET `/triggers/{id}` — Métriques de santé

```json
{
  "trigger_id": "a3f9b2e1-...",
  "name": "cpu_alert",
  "type": "resource",
  "state": "active",
  "priority": "high",
  "enabled": true,
  "condition_params": { "metric": "cpu_percent", "threshold": 85.0 },
  "health": {
    "fire_count": 12,
    "fail_count": 0,
    "throttle_count": 3,
    "last_fired_at": 1706789012.4,
    "last_error": null,
    "avg_latency_ms": 47.3
  }
}
```

---

## 10. Intégration avec l'architecture existante

### 10.1 Principe de non-rupture

**Zéro modification** des composants existants (Composants 1–7). Le TriggerDaemon s'attache de manière purement additive :

- `config.py` : nouveau bloc `TriggerConfig` avec `enabled=False` par défaut
- `api/server.py` : initialisation conditionnelle derrière `if settings.triggers.enabled`
- `events/bus.py` : inchangé — `UniversalEvent` est un wrapper opt-in
- `orchestration/executor.py` : inchangé — TriggerDaemon appelle `executor.submit_plan()` via interface existante

### 10.2 Wiring dans server.py

```python
# Startup
if settings.triggers.enabled:
    trigger_store = TriggerStore(settings.triggers.db_path)
    await trigger_store.init()
    session_propagator = SessionContextPropagator()
    trigger_daemon = TriggerDaemon(
        store=trigger_store,
        event_bus=event_bus,           # EventBus existant
        executor=executor,             # PlanExecutor existant
        session_propagator=session_propagator,
        max_concurrent_plans=settings.triggers.max_concurrent_plans,
    )
    await trigger_daemon.start()
    # Injection dans TriggerModule (via set_daemon pattern)
    trigger_module = registry.get("triggers")
    if trigger_module is not None:
        trigger_module.set_daemon(trigger_daemon)
app.state.trigger_daemon = trigger_daemon  # None si désactivé

# Shutdown
if trigger_daemon:
    await trigger_daemon.stop()
    await trigger_store.close()
```

### 10.3 Événements émis sur le bus

| Type d'événement | Topic | Déclencheur |
|---|---|---|
| `trigger.registered` | `llmos.triggers` | `daemon.register()` |
| `trigger.activated` | `llmos.triggers` | `daemon.activate()` |
| `trigger.deactivated` | `llmos.triggers` | `daemon.deactivate()` |
| `trigger.plan_submitted` | `llmos.triggers` | Plan soumis avec succès |
| `trigger.failed` | `llmos.triggers` | Watcher en erreur détecté |

---

## 11. Sécurité

### 11.1 Permissions

| Action | Permission requise | Justification |
|---|---|---|
| Créer/modifier/supprimer trigger | `power_user` | Modification de l'automatisation système |
| Lister/consulter | `local_worker` | Lecture seule, pas de risque |

### 11.2 Protection contre les boucles infinies

- `max_chain_depth` global (config) ET par trigger
- Vérification à `register()` : `chain_depth > max_chain_depth` → `ValueError`
- Valeur par défaut : 5 niveaux maximum

### 11.3 Rate limiting

- `max_fires_per_hour` : fenêtre glissante de 1h par trigger
- `min_interval_seconds` : cooldown entre deux tirs consécutifs
- Logs structurés de tous les throttles (`trigger_fire_throttled`)

### 11.4 Expiration automatique

- `expires_at` : timestamp Unix d'expiration
- `purge_expired()` : suppression automatique toutes les 30s
- Utile pour les triggers temporaires créés dynamiquement par des plans LLM

### 11.5 Sandboxing des plans déclenchés

Les plans soumis par TriggerDaemon passent par le même `PermissionGuard` que les plans soumis manuellement — aucun bypass de sécurité.

---

## 12. Configuration

### 12.1 Bloc TriggerConfig dans settings.yaml / env

```yaml
triggers:
  enabled: false                    # Désactivé par défaut
  db_path: "~/.llmos/triggers.db"  # Chemin SQLite
  max_concurrent_plans: 5          # Plans simultanés max
  max_chain_depth: 5               # Profondeur de chaînage max
  enabled_types:                   # Types de triggers autorisés
    - temporal
    - filesystem
    - process
    - resource
    - composite
```

**Variables d'environnement :**
```bash
LLMOS_TRIGGERS__ENABLED=true
LLMOS_TRIGGERS__MAX_CONCURRENT_PLANS=10
LLMOS_TRIGGERS__DB_PATH=/data/triggers.db
```

### 12.2 Dépendances optionnelles

| Type de trigger | Dépendance | Installation |
|---|---|---|
| TEMPORAL (cron) | `croniter` | `pip install llmos-bridge[triggers]` |
| FILESYSTEM | `watchfiles` | `pip install llmos-bridge[triggers]` |
| PROCESS/RESOURCE | `psutil` | Déjà requis par `os_exec` |
| Toutes les autres | Aucune | Inclus dans la distribution de base |

---

## 13. Tests et couverture

### 13.1 Fichiers de tests créés

```
tests/unit/
├── events/
│   ├── __init__.py
│   ├── test_models.py        ← UniversalEvent, EventPriority (22 tests)
│   ├── test_router.py        ← topic_matches, EventRouter (18 tests)
│   └── test_session.py       ← SessionContextPropagator (12 tests)
└── triggers/
    ├── __init__.py
    ├── test_models.py         ← TriggerHealth, TriggerDefinition, FireEvent (25 tests)
    ├── test_store.py          ← TriggerStore CRUD + persistance (20 tests)
    ├── test_watchers.py       ← Tous les watcher types (28 tests)
    ├── test_daemon.py         ← TriggerDaemon lifecycle + fire (18 tests)
    └── test_trigger_module.py ← TriggerModule avec/sans daemon (12 tests)
```

**Total :** 127 nouveaux tests
**Suite complète :** 794 tests, 0 échec

### 13.2 Scénarios critiques testés

- **Invariant store :** `update_state()` puis `get()` retourne le nouvel état (colonne SQL autoritaire)
- **Wildcard routing :** `llmos.iot.#` matche `llmos.iot` ET `llmos.iot.sensor.temperature`
- **Chaînage causal :** `spawn_child()` met à jour `parent.causes` avec l'ID enfant
- **Throttling :** `can_fire()` retourne False si `min_interval_seconds` non écoulé
- **Expiration :** `is_expired()` et `purge_expired()` fonctionnent correctement
- **Composite AND :** ne se déclenche que si tous les sous-triggers ont tiré dans le timeout
- **Daemon restart :** triggers ACTIVE rechargés et réarmés au redémarrage

---

## 14. Évolutions futures

### 14.1 Phase 2 — Watcher APPLICATION et IOT

- `ApplicationWatcher` : surveille les fenêtres X11/Wayland (via `xdotool`, `wmctrl`)
- `IoTWatcher` : intégration MQTT native + GPIO amélioré (extension du `modules/iot/`)

### 14.2 Phase 3 — Triggers distribués (multi-processus)

- Remplacer le `ConflictResolver` in-memory par Redis Streams
- Partager l'état des triggers entre plusieurs instances LLMOS Bridge
- Coordination via `RedisStreamsBus` (préparé par l'architecture EventBus Phase 1.5)

### 14.3 Phase 4 — Interface de gestion UI

- Tableau de bord web pour visualiser les triggers actifs
- Timeline graphique des events causalement liés
- Éditeur visuel de triggers (drag-and-drop conditions composites)

### 14.4 Phase 5 — Triggers LLM-natifs

- Type `SEMANTIC` : déclenché quand un texte/fichier correspond sémantiquement à un critère NLP
- Intégration avec le VectorStore existant (`memory/vector.py`)
- Exemple : "Déclenche quand un email contient une urgence"

---

## Annexe A — BaseWatcher ABC

```python
class BaseWatcher(ABC):
    def __init__(self, trigger_id, condition, fire_callback): ...

    async def start(self) -> None:
        """Lance _guarded_run() comme asyncio.Task."""

    async def stop(self) -> None:
        """Annule la tâche et attend sa complétion."""

    @abstractmethod
    async def _run(self) -> None:
        """Boucle de surveillance (implémentée par chaque watcher)."""

    async def _fire(self, event_type, payload) -> None:
        """Appelle fire_callback de manière sûre (exceptions capturées)."""

    @property
    def error(self) -> str | None:
        """Dernière exception non récupérable, None si sain."""
```

## Annexe B — WatcherFactory

```python
WatcherFactory.create(
    trigger_id="t1",
    condition=TriggerCondition(type=TriggerType.TEMPORAL, params={"interval_seconds": 60}),
    fire_callback=daemon._on_watcher_fire,
)
# → IntervalWatcher
```

Dispatch par `condition.type` :
- `TEMPORAL` → `_pick_temporal()` (CronWatcher / IntervalWatcher / OnceWatcher)
- `FILESYSTEM` → `FileSystemWatcher`
- `PROCESS` → `ProcessWatcher`
- `RESOURCE` → `ResourceWatcher`
- `COMPOSITE` → `CompositeWatcher`
- `APPLICATION`, `IOT` → `NotImplementedError` (Phase 2)

---

*Fin de l'addendum — Composant 8 TriggerDaemon & Système d'Événements Universel*
