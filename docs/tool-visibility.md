---
title: Visibilité des outils
type: reference
description: >-
  Comment la toolbox d'une session est calculée : `UserDisabledToolsMiddleware`, denylist or
  g/équipe, sélection par membre (régime nominal ADR 0019/0050), `PROTECTED_TOOLS`, refresh 
  à chaud sur bascule d'org, et la limite REST → session MCP ouverte.
---

# Visibilité des outils (per-user, org/équipe, socle)

> Extrait de `CLAUDE.md` le 2026-08-27 — le contenu n'a pas changé, seule sa place a bougé.
> La carte garde le résumé + le pointeur ; le détail (schémas, incidents datés et leurs
> leçons) vit ici.

## Le calcul de la denylist de session

`UserDisabledToolsMiddleware` (`middleware.py`) applique au handshake `initialize` les visibility rules natives fastmcp (`disable_components` via `_visibility_rules` session state). Plus de filtrage manuel `on_list_tools`/`on_call_tool` — fastmcp émet `tools/list_changed` automatiquement quand les rules changent. Le **calcul** de la denylist `(sub, org active)` + son application vivent dans **`session_visibility.py`** (`compute_hidden_tools` / `apply_session_visibility(ctx, sub, *, reset=…)`), partagés entre le middleware (handshake) et le **refresh à chaud** post-bascule.

## Source de vérité + retrait des presets

Source de vérité = tables PG `user_disabled_tools(sub, tool_name)` (négatif) + `user_enabled_tools(sub, tool_name)` (override positif). **Les presets de tools (snapshots nommés + baselines ALLOWLIST org/équipe) ont été retirés le 2026-07-03** (commit `3951a57` — masquaient tout ce qui n'était pas listé, lourd à maintenir : un tool ajouté après coup arrivait masqué par défaut pour toute baseline posée).

## Denylist org/équipe

**Remplacés depuis par un DENYLIST org/équipe** (`capabilities/tools_visibility.py`) : un org_admin/chef d'équipe masque des tools SPÉCIFIQUES par défaut pour son org/équipe (`org_disabled_tools`/`group_disabled_tools`) — le reste, y compris les tools futurs, reste visible par défaut. Additif entre paliers (union à la lecture) : une équipe ne peut jamais RÉVÉLER un tool que l'org a masqué. Gouvernance de visibilité, PAS une barrière de sécurité (ADR 0031, même esprit que `DEFAULT_HIDDEN_TOOLS`) : `user_enabled_tools` (override perso positif) lève TOUJOURS ce masquage, même échappatoire qu'un masqué-par-défaut plateforme. Calculé fail-open **indépendamment par palier** dans `session_visibility.compute_hidden_tools` (`access.org_admin_hidden_tools`/`group_admin_hidden_tools`) — un hoquet DB sur l'équipe ne prive pas l'org de son denylist. Surfaces : MCP + REST `GET/PUT/DELETE /api/{orgs,groups}/{id}/tools/{name}?/hidden`.

## Sélection par membre — régime NOMINAL (ADR 0019/0050)

**Sélection par membre = régime NOMINAL « non-sélectionné = masqué » (ADR 0019/0050).** La toolbox d'un membre = les connecteurs qu'il a **installés** (`user_selected_connectors`, per (sub, org)). Au premier profil d'un (sub, org), `session_visibility` seed le socle `providers.DEFAULT_ACTIVE_CONNECTORS` ∩ exposé — **VIDE depuis le 16/07** (décision produit : un nouveau compte démarre SANS connecteurs installés ; l'agent guide depuis les tools spine — `oto_connector` op=list/select, `oto_call` — et le catalogue injecté au bloc A) ; tout l'exposé = library installable (capacité `connectors.select`, dashboard). Les pairs pré-0050 ont été backfillés une fois avec leur visible d'alors (`connector_selection.backfill_preexisting`, sentinelle `#adr0050-backfill`). Un connecteur activé pour l'org APRÈS le seed arrive dans la library, pas dans la toolbox. Le grain CONNECTEUR `default_hidden` et les flags `OTO_CONNECTOR_SELECTION_*` ont été **retirés** (0050). **Masqués par défaut, grain OUTIL** (`is_default_hidden` = `DEFAULT_HIDDEN_TOOLS` seul : `email_send`, `fr_egapro_declaration`) : self-activables. Règle effective (`is_tool_visible`) : override positif prime > désactivé > masqué par un admin (denylist org/équipe ci-dessus) > masqué-par-défaut plateforme > visible. `oto_enable_tool` pose l'override, `oto_disable_tool` le lève (même logique côté REST `/api/me/tools/{name}`). **Stdio local (sub=None) = accès complet**, le masquage ne vise que le multi-user. Sortir un connecteur du départ = ne PAS le mettre dans le socle `default_active` ; un tool isolé = `DEFAULT_HIDDEN_TOOLS`.

## Méta-tools et `PROTECTED_TOOLS`

Méta-tools exposés (`tools/meta.py`) : `oto_list_my_tools`, `oto_disable_tool`, `oto_enable_tool`, `oto_call`, `oto_tool_schema`. **`PROTECTED_TOOLS`** (`tool_visibility.py`, source unique) = quatre familles jamais masquables (default-hidden inclus) **ni désactivables** : méta-toolset + identité (`oto_list_my_tools`/`oto_enable_tool`/`oto_whoami`/`oto_profile`), échappatoires de contexte (`oto_use_org`/`oto_clear_org`/`oto_list_orgs`/`oto_use_group`/`oto_clear_group` — anti-lockout, vécu Sentry 2026-06-30), boucle d'usage (`feedback`/`run_start`/`run_finish` — mandatés par les instructions plateforme ADR 0017 : un toggle qui les masque rend le gap invisible), **dispatch universel** (`oto_call`/`oto_tool_schema` — ADR 0036 : appeler par son nom un outil NON listé (FOD, connecteur non activé) le temps d'un appel, sans muter la visibilité ; exécution par `Tool.run` HORS middleware → gates call-time intactes + rédaction ré-appliquée via `redaction.py`). Garde des deux faces (2026-07-02) : `oto_disable_tool` refuse, `POST /api/me/tools/{name}` → 400 `protected_tool` ; `GET /api/me/tools` expose `protected:bool` (toggle inerte dashboard).

## Refresh à chaud de la toolbox

**Refresh à chaud de la toolbox sur bascule de profil** : une capacité qui change le profil de visibilité déclare `refresh_visibility=True` (`Capability`) ; l'adaptateur MCP (`capabilities/_mcp_adapter.py`) rejoue alors `apply_session_visibility(reset=True)` sur la session **courante** après le handler → `tools/list_changed` live. Posé sur `org.use_org`/`org.clear`/`org.create`/`org.set_home` + `group.use`/`group.clear`/`group.set_home`. Donc **`oto_use_org <org>` recharge la toolbox dans la conversation en cours** (les credentials, eux, basculent déjà — `resolve_api_key` relit l'org **via le seam `current_org`** à chaque appel, cf. §ADR 0023 ci-dessous).

**Limite connue** : ça ne vaut QUE pour la face MCP (même session). Un toggle/bascule via **REST** (dashboard) passe par une connexion séparée → ne notifie pas une conversation Claude déjà ouverte (visible à la prochaine session). Pousser dashboard→session MCP demanderait un registre `sub → sessions actives` + push hors-requête (non fait).
