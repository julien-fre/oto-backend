---
title: Runner hébergé & automatisations
type: reference
description: >-
  L'état du runner d'agents vit ici (fil des runs, file de jobs, déclencheurs), la boucle vi
  t dans `otomata-tech/oto-runner`. Plus le connecteur `routine` qui déclenche une routine C
  laude Code hébergée chez Anthropic.
---

# Runner hébergé & automatisations

> Extrait de `CLAUDE.md` le 2026-08-27 — le contenu n'a pas changé, seule sa place a bougé.
> La carte garde le résumé + le pointeur ; le détail (schémas, incidents datés et leurs
> leçons) vit ici.

## Runner hébergé — l'état ici, la boucle dehors (chantier R1-R5, ADR 0064 au blueprint)

Le backend porte l'ÉTAT du runner d'agents hébergé ; la BOUCLE vit dans le repo
public **`otomata-tech/oto-runner`** (worker = client pur MCP+REST, ordonnanceur
de flotte `fleet.py` — AUCUN kind serveur, la file reste uniforme ; déployé
`/opt/oto-runner` sur **`oto-platform`** (⚠️ cette carte a dit « otomata-0 »
jusqu'au 01/09/2026 : c'est faux et constaté sur la machine), gaté par le cran
`OTO_RUNNER_ARMED`). Quatre tables + leurs capacités :
- **fil des runs** `run_messages` — capacité `runs.thread` (MCP `oto_run_thread`
  + REST `/api/me/runs/thread`) : état d'exécution EFFAÇABLE (purge 30 j), append
  = propriétaire seul, read = org_admin en projection neutre (`include_raw` au
  propriétaire) ; la reprise inter-agents lit le JOURNAL, jamais le fil.
- **file de jobs** `runner_jobs` — capacité `runner.jobs` (REST-only
  `/api/me/runner/jobs`) : claim SKIP LOCKED + bail re-claimable, backoff,
  `result` JSONB déclaré à la conclusion (usage_tokens, `tool_counts` — le
  « tour perdu », un agent qui analyse sans écrire, se lit au grain job),
  op=list org-scopé (surveillance dashboard `/automations`), **paginé et
  DISANT sa borne** — voir ci-dessous.
- **flottes** `runner_fleets` (R4, 01/09/2026) — la CONFIGURATION DÉCLARÉE d'un
  passage : procédure, cible (`namespace` + `row_filter`), contexte d'exécution
  (`provider`/`model`, uniforme sur le passage — c'est LUI qui porte l'attribution
  d'une ligne écrite, l'agent ne sait pas ce qui le fait tourner), bornes
  d'exploitation (`max_rows`, `max_tokens`, `max_consecutive_failures`,
  `max_tokens_per_row` — le budget se compte en JETONS, jamais en monnaie : les
  tarifs changent et une valeur monétaire figée en base devient fausse sans que
  rien ne le dise), état + `stop_reason` ÉCRIT. `runner_jobs.fleet_id`
  rattache un travail à son passage — **posé à l'`enqueue`** (`runner.jobs
  op=enqueue fleet_id=`), rendu par `list`/`get`, et c'est lui qui rend
  `op=state` capable d'agréger. ⚠️ **L'APPARTENANCE de la flotte se vérifie, pas
  seulement son existence** : la FK dit qu'une flotte existe, pas à QUI elle est
  — sans garde, le coût d'un travail entrerait dans l'état du passage d'une autre
  org (`fleet_not_found`, même 404 sans oracle qu'un run étranger). ⚠️ Livré
  d'abord SANS écrivain (R4) : la colonne, l'index, la FK et l'agrégat existaient
  pendant que `state` répondait « aucun travail » pour toute flotte — *un harnais
  qui prouve un chemin de lecture ne prouve pas qu'il existe un chemin d'écriture
  pour ce qu'il lit* (#791, 01/09/2026). ⚠️ Une flotte vivait dans un YAML sur la
  machine : rien n'en était visible du dashboard ni atteignable par un agent.
  **Déclarer n'est pas restreindre — c'est donner un domicile aux gardes** : un
  lancement qui prend son tableau en argument n'a nulle part où accrocher une
  cible ni une borne. ⚠️ `heartbeat_at` distingue le VIVANT du RÉSIDU (une flotte
  `running` qui ne bat plus n'est pas une concurrence à attendre), et la table est
  créée AVANT `runner_jobs`, qui la référence.
  ⚠️ **SEPT états, parce que deux d'entre eux séparent une INTENTION d'un FAIT**
  (R4b, 01/09/2026) : `armed` (on a DEMANDÉ que ça tourne, `op=launch`) ≠
  `running` (un ordonnanceur l'a PRISE et donne signe) ; `stopping` (arrêt
  demandé, `op=stop`) ≠ `stopped` (l'ordonnanceur a ACCUSÉ réception). *Une
  intention déclarée et un fait constaté ne partagent jamais une colonne* — sans
  `armed`, une flotte que personne n'a réclamée se lirait « en cours » ; sans
  `stopping`, un arrêt demandé se lirait « arrêté », et **croire qu'on a coupé
  une dépense qui continue est pire que croire qu'on a lancé un passage qui ne
  tourne pas**. L'écart entre les deux EST le diagnostic : un `stopping` qui ne
  devient jamais `stopped` désigne un ordonnanceur mort.
  ⚠️ **Deux planchers, parce que la garde suit ce que le geste ENGAGE** :
  `launch` est réservé aux **admins** de l'org (il engage une dépense et des
  écritures chez un tiers, irréversibles) ; `stop` est ouvert à **tout membre**
  (un passage qui part en vrille doit pouvoir être stoppé par la première
  personne qui le voit). Deux gardes distinctes : *un déroulé ne LANCE pas* (un
  agent qui se relance dépense en boucle) et *un déroulé n'arrête pas CELLE QUI
  L'EXÉCUTE* — nommée, plutôt que de fermer le verbe à tout le monde.
- **déclencheurs** `runner_triggers` — capacité + MCP `oto_trigger`, tick
  backend avec CAS sur `next_due` (prod/preprod partagent la base : un seul
  gagnant par échéance).
⚠️ **« Arrêter » vise DEUX services distincts** (constaté le 01/09/2026) :
l'ordonnanceur (`oto-fleet-<nom>`) cesse d'ENFILER, les agents (`oto-runner@1..N`,
unités séparées) finissent ce qui est pris **et restent ARMÉS sur la file**.
Arrêter le premier laisse les seconds prêts à repartir, et des écritures tombent
jusqu'à plusieurs minutes après un « c'est arrêté » qui n'a regardé que
l'ordonnanceur. **« Rien ne tourne » ne se dit qu'après avoir constaté les deux.**
⚠️ Les jetons de contexte (`_project`…) sont advertisés PAR TOOL : un client
les pose d'après le schéma du tool, jamais à l'aveugle (un jeton non déclaré
fait refuser l'appel entier à la validation). Conception + état des preuves :
blueprint `chantier-runner.md` ; pilote = une campagne cliente (fusion R5, 14/08).

### `op=list` : la page dit ce qu'elle laisse dehors (#469, 01/09/2026)

**Mesuré le 28/08** : `POST /api/me/runner/jobs {op: list, limit: 1000}` rendait
**200** lignes. La borne était appliquée dans le `LIMIT` du SQL
(`db/runner_jobs.py`), sans être déclarée nulle part et sans que la réponse ne
l'annonce : ni total, ni curseur. Un poste de flotte qui faisait le bilan d'une vague
de 150+ jobs lisait donc `len(jobs)` comme le compte de la file — et lisait faux.

⚠️ **Un relevé plafonné SOUS-déclare : il rend moins d'anomalies que la réalité,
jamais plus.** C'est la classe de défaut qui rassure exactement quand il ne faut pas,
et c'est pour ça qu'elle vaut mieux qu'une gêne d'ergonomie. Le runner s'en était
affranchi par un bilan natif côté client — une rustine qui masque le défaut au lieu
de le fermer, et qui ne protège aucun autre consommateur de la route.

La page porte donc deux champs, et ils vont ensemble :
- **`total`** — le nombre de jobs de la file sous les MÊMES filtres (org + `status`),
  indépendant de `limit` et de la position du curseur. C'est le dénominateur d'un
  bilan ; il ne bouge pas d'une page à l'autre.
- **`next_cursor`** — opaque, à renvoyer tel quel dans `cursor` pour lire la page
  suivante (plus ancienne) ; `null` = fin de la file. **Une page pleine AVEC un
  `next_cursor` dit que la lecture est tronquée ici.**

Le curseur est un **keyset** sur l'ordre servi (`id DESC`), pas un OFFSET : une file
bouge sous la marche, et un job enfilé entre deux pages décalerait tout un OFFSET —
donc ferait sauter une ligne, c'est-à-dire recréerait la sous-déclaration qu'on
ferme. Un curseur illisible est un **refus nommé** (`400 invalid_cursor`), jamais un
repli muet sur le début de la file : rejouer la première page en boucle est
indiscernable d'une marche qui progresse.

La borne (`JOBS_PAGE_MAX = 200`) reste appliquée dans le SQL en dernier ressort, mais
celle qui ENGAGE est désormais au contrat (`capabilities/runner_jobs.py`, patron
`cap_limit` : on écrête, on ne refuse pas) — et l'écrêtage n'est plus muet.

### `complete` libère les baux du run et rend le compte — `0` écrit (#633, 29/08/2026)

**Mesuré sur une campagne** : un poste de flotte lit « le témoin que la clôture du
travail rend » — or `op=complete` ne libérait aucune ligne du datastore et rendait
`{"ok", "status"}` sans compte. La libération ne jouait que sur `run_finish`, l'appel
de l'**agent** — qui rendait `rows_released` seulement s'il y avait au moins une ligne
(absent = zéro). Un agent mort sans `run_finish` laissait sa ligne au bail jusqu'à
expiration ; le **worker**, lui, survit à l'agent et conclut le job : c'est là que la
libération manquait.

**Depuis #633**, `complete` libère les baux du run que le job connaît — le `run_id` de
l'appel d'abord, sinon celui posé par `bind_run` (ou un `continue`) — par
`datastore_release_by_run`, **quel que soit `ok`** (un job qui repart en file avec
backoff ne travaille plus non plus ; la ligne revient dans la file, la reprise la
reprendra). Best-effort et HORS de la clôture, comme `run_finish` : le job est conclu
d'abord, la libération est un service rendu ensuite. La réponse porte trois champs
déclarés dans l'`Output` (donc dans l'OpenAPI) :

| forme | sens |
|---|---|
| `run_id: "…", rows_released: 2, release: "ok"` | le run tenait 2 lignes, rendues |
| `run_id: "…", rows_released: 0, release: "ok"` | le run ne tenait rien — **le 0 est écrit** |
| `run_id: null, rows_released: null, release: "no_run"` | aucun run connu du job : rien à libérer par run, rien n'est fabriqué |
| `run_id: "…", rows_released: null, release: "failed"` | la libération a échoué (journal serveur) ; le job est conclu, les baux expirent seuls |

`run_finish` écrit lui aussi `rows_released` **toujours** (`0` explicite ; `null` si la
libération a échoué) — sa description ne change pas, c'est la réponse. Preuves :
`tests/test_complete_releases_633.py` (chemin réel : réservation par le middleware +
`data_claim_next` monté, capacité `runner.jobs` telle que la route l'appelle, PostgreSQL)
et `tests/test_run_finish_releases_613.py`. ⚠️ `runner_jobs.run_id` référence `runs`
(FK) : un job ne se lie qu'à un run qu'un `run_start` a ouvert.

### Ce qu'un écran de surveillance lit d'un travail (01/09/2026)

Deux manques de la même famille — une donnée que la plateforme détenait déjà et qui ne
sortait pas.

**Le bail, sur `list` et `get`.** `lease_until` n'était rendu que par `op=claim`,
c'est-à-dire au seul worker qui vient de prendre le job ; les deux verbes de
surveillance ne le sélectionnaient pas. Un écran ne pouvait donc pas dire « ce bail a
expiré », seulement « ce travail traîne depuis longtemps » — un **seuil dérivé** de
l'ancienneté, qui range dans la même case un travail lent et un travail mort. La
colonne porte la DATE ; c'est au lecteur de la comparer à l'heure qu'il est, **contre
le statut** :

| statut | `lease_until` |
|---|---|
| `pending` jamais pris | `null` |
| `claimed` | la fin du bail en cours — passée = le worker est parti, le job est re-claimable (`attempts` compte chaque prise) |
| `done` | le bail qui ÉTAIT tenu, laissé tel quel |
| échec re-filé | `null` — la prise est rendue en même temps que le job |

**Les postes de garde du harnais, au contrat.** `result` est ouvert (`extra=allow`) :
le worker y déclare bien plus que les quatre champs du socle, et tout est **servi**.
Mais servi n'est pas **déclaré** — un client typé (les types générés du dashboard,
dérivés de l'OpenAPI) ne voit que ce que le schéma nomme, et rien ne garantit la forme
de ce qu'il ne nomme pas. Trois champs sont désormais nommés sur `JobResult`, parce que
leur forme porte un sens qu'un client peut se tromper en lisant :

| champ | forme | ce que `null` veut dire |
|---|---|---|
| `valeurs_cliente_reparees` | liste de colonnes remises en place depuis `<colonne>.origine` | — (`[]` = rien à réparer) |
| `contacts_fabriques_retires` | liste de contacts fabriqués RETIRÉS de la ligne | — (`[]` = aucun) |
| `valeurs_cliente_detruites` | liste de colonnes détruites, **ou `null`** | ⚠️ **NON MESURÉ** : le harnais n'a pas pu identifier la ligne travaillée, la garde n'a pas tourné |

⚠️ `valeurs_cliente_detruites: null` **n'est pas** `[]`. Le lire comme « aucune
destruction » afficherait un travail propre là où personne n'a regardé — et c'est le cas
FRÉQUENT, pas le cas limite : sur le chemin « conversations » le harnais retrouve sa
ligne par alias, et ce recours échoue dès qu'elle est relâchée. Preuves :
`tests/test_runner_jobs_travail_servi.py`.

**Les autres champs de `result` restent indéclarés**, et c'est un manque connu, pas un
choix : `writes`, `claims`, `model`, le détail de coût (`usage_input`/`usage_output`/
`usage_cache_read`/`usage_cache_write`), `hors_schema`, `hors_perimetre`, `claims_mesures`, `claim_vide`,
`faux_depart`, `estampille`, `renvois`, `abandon_enregistre`, `rappel_contact_mesure`,
`rappels_contact`, `effectif_non_atteste`, `contact_rattrape`, `contact_arbitre`,
`ligne_abandonnee`. Ils traversent par `extra=allow` et un client typé ne les voit pas.

## Automatisations — déclencher une routine Claude Code (v1.73.0)

Connecteur `routine` (`routine_fire.py` + capacité `me.automation.fire`, MCP
`routine_fire` / REST `POST /api/me/automations/fire`) : **une instance = une routine**
hébergée chez Anthropic (`routine_id` + jeton de déclenchement en `credential_fields`),
parce que le jeton `/fire` est scopé par Anthropic à une seule routine. L'appel ne bloque
pas — il crée la session et rend son URL ; le résultat se lit **dans la session**.
Le `text` arrive à l'agent enveloppé `<routine-fire-payload>` étiqueté DONNÉE NON FIABLE
(le prompt de la routine doit opter pour le lire) ⟹ passer une **référence**, jamais
l'enregistrement. Montage complet côté utilisateur = guide plateforme
**`procedure-en-routine`**.

⚠️ **Ce connecteur relaie, il n'apporte rien d'autre** : un tiers qui sait faire un POST
appelle `/fire` en direct. Son seul cas réel est *un agent en conversation qui déclenche
une automatisation*. Il ne vaudra plus que ça tant qu'oto ne fait rien entre les deux
(tracer les tirs, router selon l'événement, dédupliquer). **Aucune API publique de
création de routine ni de génération de jeton** — le provisionnement reste manuel, par
construction ; l'état vide de la page Automatisations du dashboard l'explique.
