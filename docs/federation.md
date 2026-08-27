---
title: Fédération MCP & comptes (otomata#16)
type: explanation
description: >-
  Explique les deux mécanismes de fédération MCP coexistants dans oto-backend : mount
  (kind="mount", tools natifs du MCP distant, token OAuth per-user injecté par requête,
  pilotes atlassian/planity)
  et remote (ADR 0003, tunnel <ns>_describe/<ns>_call data-driven, credential M2M d'org,
  pilote = un connecteur remote client). Détaille l'activation
  des mounts (connector_activation ∪ OTO_MCP_MOUNTS_ENABLED), et la limite catalogue figé
  au boot (refresh via oto_admin_refresh_mount). À lire pour comprendre pourquoi/comment
  un MCP distant est monté, ou pour ajouter un nouveau mount ou bridge.
adr:
  - "0003"
---

# Fédération MCP & comptes (otomata#16)

Deux mécanismes de fédération coexistent (cf. `tools/mount.py` vs `tools/remote.py`) :
- **mount** (`kind="mount"`) — monte les outils NATIFS d'un MCP distant (vrais schémas,
  `<ns>_<tool>`), credential **per-user** (token OAuth) injecté par requête. Pilote = **atlassian**.
- **remote** (ADR 0003, data-driven) — tunnel `<ns>_describe`/`<ns>_call` vers un bridge, credential
  d'**org** (token M2M + `meta.base_url`). Pilote = un connecteur remote client.

## Mount SANS auth (endpoint hébergé public)

Un mount `kind="mount"` dont `auth_modes` est **VIDE** est un **mount no-auth** :
l'endpoint distant est hébergé et **public** (aucune clé, aucun compte, catalogue
product-level anonyme).

⚠️ **Aucun connecteur ne prend ce chemin aujourd'hui.** Son unique pilote,
`justicelibre`, a été retiré le 2026-08-21 (voir la note en fin de page). La
branche décrite ci-dessous reste dans `tools/mount.py`, générique et sans
consommateur vivant : elle est verrouillée par `tests/test_mount_noauth.py`, qui
l'exerce sur un mount **synthétique** et tombe si un mount no-auth revient au
registre sans sa couverture.

`tools/mount.py` détecte `not connector.auth_modes` et prend un chemin dédié qui
**court-circuite tout le machinery per-user** :
- `_fetch_catalog` liste les outils au boot **sans token ni user connecté** (le
  fallback historique « token d'un user déjà connecté » ne s'applique pas) ;
- `_make_factory` renvoie une factory qui forwarde **sans header `Authorization`**,
  **sans `resolve_mount_token`** et sans exiger un sub courant.

**Gating d'exposition** = `connector_activation` (ADR 0010/0011), comme n'importe
quel connecteur — c'est le SEUL levier ici (pas de credential per-user à connecter).
Un mount no-auth serait **opt-in par org** : master OFF au registre d'activation,
hors `_DEFAULT_ENABLED_MOUNTS`, hors bundle par défaut. Une org l'active via l'écran
connector activation → le mount **suit** (`_db_activated_mounts` inclut tout mount
ayant ≥1 activation ON, master OU override d'org). Comme le catalogue est **figé au
boot**, la 1ʳᵉ activation demande un **restart** OU un `oto_admin_refresh_mount
<nom>` (admin plateforme) pour le monter à chaud.

Dégradation propre : endpoint distant down au boot → 0 outil fédéré, le reste d'oto
intact (le fetch est sous try/except). Test : `tests/test_mount_noauth.py`.

**Connecteur memento — RETIRÉ le 2026-07-30.** Le produit Memento est décommissionné
(la mémoire est native : `oto_kb` / `oto_doc`). Ont été supprimés : l'entrée de registre
`memento`, `memento_oauth.py`, `memento_federation.py` (le provisioning automatique du
compte à l'inscription), `api_routes_memento.py` et les routes `/api/memento/*`, la clé
`memento` de `/api/me`. Les env `MEMENTO_*` de la box n'ont plus de lecteur.

**Connecteur justicelibre — RETIRÉ le 2026-08-21.** Mount no-auth vers
`justicelibre.org/mcp` (droit français & européen : LEGI/JORF/KALI + jurisprudence
Cass/Judilibre/CE/CC/CEDH/CJUE/CNIL, MIT + Licence Ouverte Etalab 2.0). La
fédération MCP est en sommeil, son master était **OFF en prod** et le connecteur
n'était plus qu'un reste. Ont été supprimés : l'entrée de registre `justicelibre`
et la couverture de test qui s'appuyait sur lui. **Ce qui RESTE** : la branche
no-auth de `tools/mount.py`, désormais générique — un futur mount public la
reprendra telle quelle. ⚠️ Des lignes `justicelibre` peuvent subsister en base
(activation, credentials) : elles sont inertes sans entrée de registre, leur
nettoyage est une opération distincte.

État courant du montage :
- **Plus aucun mount monté d'office.** `_DEFAULT_ENABLED_MOUNTS` est **vide** ; un mount
  se monte via le régime commun `connector_activation` (master/override ON) ∪ env
  `OTO_MCP_MOUNTS_ENABLED` (`*` = tous, CSV = liste, `""` = kill-switch absolu). En prod :
  master atlassian **OFF**, env = `planity` seul.
- Limite inchangée : catalogue mount figé au boot (≥1 credential connecté requis pour
  le charger).
