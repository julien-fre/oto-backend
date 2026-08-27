"""Rôles + résolution de clé API + quotas par tool.

Le rôle `users.role` décide de l'accès à l'admin UI, sur **3 paliers** (du plus
faible au plus fort) :

- **member** : rôle par défaut (non-admin), sans effet sur l'accès aux
  tools. L'accès se décide via les `user_grants` (cf. ci-dessous).
- **admin** (palier OPÉRATIONNEL intermédiaire) : supervision plateforme —
  liste des users, fiche user, monitoring des appels, activation des
  connecteurs, maintenance (refresh des mounts), lecture/admin opérationnelle
  des orgs. **PAS** d'escalade en masse vers les orgs tierces.
- **super_admin** (le tout-puissant) : tout l'opérationnel + escalade
  `org_admin` de TOUTES les orgs et `group_admin` de TOUS les groupes,
  gestion des rôles plateforme, platform keys, émission de tokens, écriture
  sur les orgs tierces (entitlements, doctrine d'une autre org), création d'org.
  Bootstrap : env `OTO_MCP_ADMIN_SUB` force ce sub en **super_admin** quoi
  qu'il y ait en DB.

Résolution d'une clé API par appel (`resolve_api_key`) :

1. Si user key posée par le user lui-même sur `/account` → on la prend,
   sans quota.
2. Sinon, on cherche un grant explicite dans `user_grants` (admin a posé
   une autorisation) → on prend la `platform_keys.api_key` la plus
   récemment grantée.
3. Sinon (et y compris pour un admin sans grant) → McpError actionnable.

Quota daily : chaque grant porte un `daily_quota` optionnel (per-user,
posé par l'admin au moment du grant). Si null, fallback sur
`OTO_MCP_QUOTA_<PROVIDER>_DAILY` env ou `_QUOTA_DEFAULTS`.

Les clés plateforme vivent en DB (coffre `platform_keys`) — posées/rotées via la
surface admin (REST `/api/admin/platform-keys`, meta-tools `oto_admin_*`), plus
aucun import SOPS/env au boot (oto-mcp#12). Importer ≠ auto-granter : une clé
n'est accessible qu'avec un grant admin explicite.

## Le package (découpe du 2026-08-27) — où vit quoi

`access.py` faisait 2 000 lignes et concentrait quatre sujets qui ne se lisent
pas ensemble. Le fichier étant l'unité d'occupation d'une session sur un tree
partagé, il était aussi le goulot de tous les chantiers de connecteurs. La
découpe est un **DÉPLACEMENT PUR** : aucun appelant ne change (cf.
`tests/test_access_surface_frozen.py`).

| module      | ce qu'il porte                                                  |
| ----------- | --------------------------------------------------------------- |
| `scope`     | qui agit, sous quelle org/équipe/projet ; rôle plateforme ; ce que le projet ÉPINGLE ; `_UNSET` |
| `quotas`    | ce qui est métré (quota jour, usage) et ce qui est payé (option, comp, abonnement) |
| `cascade`   | le walker UNIQUE `perso > cross-org > équipe > org > plateforme`, ses trois sondes, le palier plateforme |
| `rbac`      | qui a le droit : RBAC connecteur org/équipe, tools masqués, garde d'instance, instances à portée, redaction |
| `resolve`   | la résolution réelle d'un credential (chemin chaud) + l'endpoint anonyme |
| `views`     | les vues minces : clé, champs, mount, mode, option levée, résolvabilité d'une org |
| `status`    | le snapshot par connecteur de `/api/me`                          |

Le graphe est un DAG strict — aucun cycle, chaque flèche va vers le bas :

```
                    scope                    (ne dépend de rien)
                   ↗  ↑  ↖
            quotas   cascade                 (cascade → scope)
                ↑     ↑  ↖
                |    rbac                    (rbac → scope, cascade)
                |   ↗   ↑
              resolve   |                    (resolve → scope, quotas, rbac, cascade)
                ↑       |
              views   status                 (status → scope, quotas, rbac, cascade)
```

## La surface reste PLATE, et le point de patch reste `access.<nom>`

Deux mécanismes, tous les deux ici et nulle part ailleurs :

1. **Ré-export plat** — `access.<nom>` rend ce qu'il rendait avant la découpe,
   privés compris (`_UNSET`, `_resolve_credential_impl`, `_platform_grant_meta`…
   sont consommés à l'extérieur). Même idiome que le package `db`.
2. **Propagation des écritures** — un sous-module appelle son voisin par le
   MODULE (`scope.current_org(...)`), jamais par un nom importé : c'est ce qui
   dit au lecteur d'où vient la fonction. Mais alors une écriture sur la façade
   (`monkeypatch.setattr(access, "current_org", …)`, l'idiome de ~200 endroits
   de la suite) n'atteindrait plus l'intérieur du package : le voisin lirait
   toujours l'original. La façade propage donc toute écriture aux sous-modules
   qui DÉFINISSENT ce nom. Sans ça, le déplacement changerait le comportement de
   tests qu'il n'était pas censé toucher — et il le changerait en SILENCE, en
   les laissant verts sur un chemin qui n'est plus celui qu'ils croient exercer.
"""
from __future__ import annotations

import logging
import sys
import types

from . import scope, quotas, cascade, rbac, resolve, views, status

_MODULES = (scope, quotas, cascade, rbac, resolve, views, status)

# Ré-export plat (publics + privés à un underscore ; les dunder restent au
# package) + carte `nom -> modules qui le définissent`, qui sert la propagation
# ci-dessous. Les noms sont disjoints entre modules, à l'exception des modules
# importés en commun (`db`, `connectors`…) : là, tous les porteurs sont notés,
# et une écriture sur la façade les atteint tous — comme quand ils n'étaient
# qu'un seul module.
_OWNERS: dict = {}
_g = globals()
for _mod in _MODULES:
    for _name in dir(_mod):
        if _name.startswith("__"):
            continue
        _g[_name] = getattr(_mod, _name)
        _OWNERS[_name] = _OWNERS.get(_name, ()) + (_mod,)
del _g, _mod, _name

# `access.logger` reste le logger du NOM `oto_mcp.access` (la boucle ci-dessus
# aurait laissé celui du dernier sous-module).
logger = logging.getLogger(__name__)


class _Facade(types.ModuleType):
    """Le module `access` lui-même, avec la propagation d'écriture (cf. §2 du
    docstring). `__delattr__` n'est PAS surchargé : retirer un nom de la façade
    ne doit pas décapiter le sous-module qui le sert."""

    def __setattr__(self, name, value):
        super().__setattr__(name, value)
        for mod in _OWNERS.get(name, ()):
            setattr(mod, name, value)


sys.modules[__name__].__class__ = _Facade
