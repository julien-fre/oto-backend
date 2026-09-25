"""Les déclencheurs du runner — la config qui fabrique des jobs (chantier R3).

Le module ne connaît pas le cron : il stocke, liste, et surtout **consomme une
échéance par compare-and-swap** — prod et preprod partagent la même base, deux
ticks tournent, un seul doit gagner chaque échéance. Le calcul de la prochaine
échéance (croniter, dans le fuseau du déclencheur) vit dans `runner_tick`, à un
seul endroit.
"""
from __future__ import annotations

import json
from typing import Any, Optional

from .. import runner_models
from ._conn import _connect

_COLS = ("id, org_id, sub, label, procedure, project_id, tools, input, max_steps, "
         "max_tokens, max_run_seconds, model, kind, payload_mode, payload_fields, max_per_hour, fraicheur_s, "
         "cron, tz, enabled, next_due, last_enqueued_at, created_at")

#: ⚠️ `hook_secret_hash` n'est PAS dans `_COLS`, et c'est la garde : un haché servi
#: à une lecture partirait dans la réponse de `op=list`, donc dans un transcript
#: d'agent. La route le lit par une requête dédiée (`trigger_par_secret`), et le
#: secret en clair n'existe qu'une fois, au retour de `poser_secret_de_hook`.


def create_trigger(org_id: int, sub: str, *, procedure: str, tz: str,
                   tools: list, cron: Optional[str] = None, next_due=None,
                   project_id: Optional[int] = None,
                   input: Optional[str] = None, label: Optional[str] = None,
                   max_steps: Optional[int] = None,
                   max_tokens: Optional[int] = None,
                   max_run_seconds: Optional[int] = None,
                   model: Optional[str] = None,
                   kind: str = "schedule",
                   payload_mode: str = "ignore",
                   payload_fields: Optional[dict] = None,
                   max_per_hour: Optional[int] = None,
                   fraicheur_s: Optional[int] = None) -> dict:
    """Pose un déclencheur — programmé (`cron` + `next_due`) ou par webhook.

    ⚠️ `cron` et `next_due` sont devenus FACULTATIFS en signature, et c'est la
    seule forme qui dise la vérité : un déclencheur par webhook n'a pas
    d'échéance. Ce que la capacité exige de l'un ou de l'autre est sa règle à
    elle — ici on stocke ce qu'on reçoit."""
    with _connect() as conn:
        row = conn.execute(
            f"""
            INSERT INTO runner_triggers
                   (org_id, sub, label, procedure, project_id, tools, input,
                    max_steps, max_tokens, max_run_seconds, model, kind, payload_mode,
                    payload_fields, max_per_hour, fraicheur_s, cron, tz, next_due)
            VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s, %s, %s, %s,
                    %s::jsonb, %s, %s, %s, %s, %s)
            RETURNING {_COLS}
            """,
            (org_id, sub, label, procedure, project_id,
             json.dumps(list(tools), ensure_ascii=False), input, max_steps,
             max_tokens, max_run_seconds, model, kind, payload_mode,
             json.dumps(payload_fields, ensure_ascii=False) if payload_fields else None,
             max_per_hour, fraicheur_s, cron, tz, next_due),
        ).fetchone()
    return dict(row)


def list_triggers(org_id: int) -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            f"SELECT {_COLS} FROM runner_triggers WHERE org_id = %s ORDER BY id",
            (org_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def get_trigger(trigger_id: int, org_id: int) -> Optional[dict]:
    with _connect() as conn:
        row = conn.execute(
            f"SELECT {_COLS} FROM runner_triggers WHERE id = %s AND org_id = %s",
            (trigger_id, org_id),
        ).fetchone()
    return dict(row) if row else None


def update_trigger(trigger_id: int, org_id: int, champs: dict[str, Any], *,
                   hors_abonnement_d_autrui: Optional[str] = None) -> Optional[dict]:
    """Mise à jour partielle, org-scopée. `champs` ne contient QUE des colonnes
    déjà validées par la capacité (jamais de SQL construit sur l'entrée brute).

    `hors_abonnement_d_autrui=<sub>` : n'écrit PAS un déclencheur posé sur l'abonnement
    d'une autre personne que `<sub>` (rend None, comme un déclencheur inconnu). La
    garde vit dans l'écriture pour qu'une retouche ordinaire ne relise rien : c'est
    l'appelant qui relit, et seulement quand rien n'a été écrit."""
    autorises = {"label", "procedure", "project_id", "tools", "input", "max_steps",
                 "max_tokens", "max_run_seconds", "model", "cron", "tz", "enabled", "next_due",
                 # Le webhook. ⚠️ `kind` n'y est PAS : un déclencheur ne change pas
                 # de coup d'envoi en cours de route — ce serait un autre agent, et
                 # la bascule laisserait derrière elle soit un cron orphelin, soit
                 # un secret qui ouvre une porte que plus personne ne regarde.
                 "payload_mode", "payload_fields", "max_per_hour", "fraicheur_s"}
    inconnu = set(champs) - autorises
    if inconnu:
        raise ValueError(f"colonnes hors contrat : {sorted(inconnu)}")
    if not champs:
        return get_trigger(trigger_id, org_id)
    sets, vals = [], []
    for k, v in champs.items():
        if k == "tools":
            sets.append("tools = %s::jsonb")
            vals.append(json.dumps(list(v), ensure_ascii=False))
        elif k == "payload_fields":
            # Un dict nu ne s'adapte pas en jsonb (psycopg refuse `dict`) : le
            # passer tel quel faisait échouer TOUTE mise à jour qui le portait.
            sets.append("payload_fields = %s::jsonb")
            vals.append(json.dumps(v, ensure_ascii=False) if v else None)
        else:
            sets.append(f"{k} = %s")
            vals.append(v)
    with _connect() as conn:
        row = conn.execute(
            f"UPDATE runner_triggers SET {', '.join(sets)} "
            f"WHERE id = %s AND org_id = %s"
            + ("" if hors_abonnement_d_autrui is None else
               " AND (model IS NULL OR NOT (model = ANY(%s)) OR sub = %s)")
            + f" RETURNING {_COLS}",
            (*vals, trigger_id, org_id,
             *(() if hors_abonnement_d_autrui is None else
               (list(runner_models.MODELES_PERSONNELS), hors_abonnement_d_autrui))),
        ).fetchone()
    # ⚠️ ÉTEINDRE, c'est aussi cesser de tiquer — donc cesser de périmer. Un
    # déclencheur désactivé laissait ses occurrences en attente pour toujours,
    # et le geste qui les rendait éternelles était précisément celui par lequel
    # quelqu'un cherchait à arrêter les dégâts. **Le seul geste de réparation
    # disponible aggravait la panne, en silence.**
    #
    # ⚠️ **Et un agent DÉCLENCHÉ, lui, ne perd rien** (13/09/2026). L'asymétrie est
    # voulue : l'occurrence d'un agent programmé a un SUCCESSEUR, et la jouer trop
    # tard rend un résultat faux ; **un événement n'en a pas** — personne ne
    # renverra le lead d'hier. Sa file est donc GELÉE (`held`), invisible aux
    # workers tant que l'agent dort, et rendue telle quelle au rallumage. Vider,
    # c'est un geste explicite et séparé (`runner.triggers op=clear_queue`).
    if row and champs.get("enabled") is False:
        if (row.get("kind") or "schedule") == "webhook":
            from .runner_hooks import suspendre_la_file
            suspendre_la_file(trigger_id, org_id)
        else:
            from .runner_jobs import perimer_travaux_du_declencheur
            perimer_travaux_du_declencheur(
                trigger_id, org_id,
                raison="déclencheur désactivé : ses occurrences en attente ne seront "
                       "jamais exécutées.")
    if row and champs.get("enabled") is True and (row.get("kind") or "") == "webhook":
        from .runner_hooks import reprendre_la_file
        reprendre_la_file(trigger_id, org_id)
    return dict(row) if row else None


def trigger_par_secret(trigger_id: int, secret_hash: str) -> Optional[dict]:
    """Le déclencheur webhook d'un id ET d'un secret — les deux, ou rien.

    ⚠️ La comparaison du haché est faite EN SQL, dans le même `WHERE` que l'id :
    un `SELECT` par id suivi d'une comparaison en Python distinguerait « id
    inconnu » de « mauvais secret » par le temps de réponse, et rendrait la route
    bavarde sur les ids qui existent. Ici les deux cas rendent `None`.

    ⚠️ `enabled` n'est PAS filtré : un déclencheur en pause doit être TROUVÉ pour
    que la route réponde 409 (« il existe, il est en pause ») plutôt que 404. La
    pause est une information que son propriétaire a le droit de recevoir — c'est
    lui qui a donné le secret à la source.
    """
    with _connect() as conn:
        row = conn.execute(
            f"SELECT {_COLS} FROM runner_triggers "
            f"WHERE id = %s AND kind = 'webhook' AND hook_secret_hash = %s",
            (trigger_id, secret_hash),
        ).fetchone()
    return dict(row) if row else None


def poser_secret_de_hook(trigger_id: int, org_id: int, secret_hash: str) -> bool:
    """Pose (ou remplace) le haché du secret. Le clair ne passe jamais par ici."""
    with _connect() as conn:
        cur = conn.execute(
            "UPDATE runner_triggers SET hook_secret_hash = %s "
            "WHERE id = %s AND org_id = %s AND kind = 'webhook'",
            (secret_hash, trigger_id, org_id),
        )
        return bool(cur.rowcount)


def delete_trigger(trigger_id: int, org_id: int) -> bool:
    """Supprime le déclencheur — et PÉRIME d'abord ce qu'il a laissé en attente.

    ⚠️ **La péremption ordinaire passe par le TICK du déclencheur : un déclencheur
    qui ne tique plus ne périme plus rien.** Supprimé, il laissait donc ses
    occupations en `pending` pour toujours — et pire qu'avant, puisque le
    compteur de pertes se lit SUR le déclencheur : elles devenaient invisibles en
    même temps qu'éternelles. Le jour où des agents arrivent sur cette org, elles
    partiraient, pour un déclencheur que plus personne n'a.

    C'est la forme générale du piège : *« ne pas toucher » n'est une conservation
    que si quelque chose garantit la cible.* Ici rien ne la garantit — il n'y a
    pas de clé étrangère entre un travail et son déclencheur, seulement un
    identifiant recopié dans la charge.
    """
    from .runner_jobs import perimer_travaux_du_declencheur
    with _connect() as conn:
        cur = conn.execute(
            "DELETE FROM runner_triggers WHERE id = %s AND org_id = %s",
            (trigger_id, org_id),
        )
        supprime = bool(cur.rowcount)
    if supprime:
        perimer_travaux_du_declencheur(
            trigger_id, org_id,
            raison="déclencheur supprimé : ses occurrences en attente ne seront "
                   "jamais exécutées.")
    return supprime


def triggers_for_procedure(org_id: int, procedure: str) -> list[dict]:
    """Les déclencheurs d'UN objet — ce que l'écran d'une procédure doit savoir.

    ⚠️ Les déclencheurs ne se listaient que par ORGANISATION. La page d'une
    procédure ne pouvait donc pas dire si elle tourne toute seule : il aurait
    fallu charger tous les déclencheurs de l'org et filtrer côté client, ce qui
    devient faux dès qu'il y en a plus d'une page.

    L'agent programmé est une PROPRIÉTÉ de l'objet (direction du 02/09), pas un
    objet séparé — donc il doit se lire depuis l'objet.
    """
    with _connect() as conn:
        rows = conn.execute(
            f"SELECT {_COLS} FROM runner_triggers "
            f"WHERE org_id = %s AND procedure = %s ORDER BY id DESC",
            (org_id, procedure),
        ).fetchall()
    return [dict(r) for r in rows]


def triggers_actifs_utilisant(org_id: int, connector: str) -> list[dict]:
    """Les déclencheurs ACTIFS de l'org dont les outils dépendent de `connector`.

    Ce que ça sert : quand quelqu'un retire une clé, personne ne lui dit que des
    agents programmés en dépendent. Le 03/09/2026, une clé a disparu et **une douzaine
    de passages programmés ont tourné à l'aveugle pendant 36 h** — et le canal qui
    aurait annoncé la panne tournait sur le credential tombé, donc la panne était
    **silencieuse par construction** (oto#59, signaux 672 et 710).

    ⚠️ **Ne rend que les dépendances DÉCLARÉES**, celles qui passent par la liste
    d'outils du déclencheur. Un agent programmé créé depuis un objet dérive ses outils
    de la procédure : la dépendance y est réelle mais implicite, et cette lecture ne
    la voit pas. Elle sous-estime donc, jamais l'inverse — un résultat vide se lit
    « je n'en connais pas », pas « il n'y en a pas ».

    Le rattachement outil → connecteur passe par `namespace_of`, le seam qui gouverne
    déjà les gates d'appel : refaire ici une correspondance par préfixe ferait diverger
    les deux au premier connecteur multi-token.
    """
    from ..tool_visibility import namespace_of

    out = []
    for t in list_triggers(org_id):
        if not t.get("enabled"):
            continue
        outils = t.get("tools") or []
        if any(namespace_of(str(o)) == connector for o in outils if o):
            out.append(t)
    return out


def due_triggers(limit: int = 50) -> list[dict]:
    """Les déclencheurs à échéance — lecture nue, TOUTES orgs (le tick est un
    service de plateforme). La consommation se fait par CAS, pas ici."""
    with _connect() as conn:
        rows = conn.execute(
            f"SELECT {_COLS} FROM runner_triggers "
            # ⚠️ `kind = 'schedule'` en plus de l'échéance : un déclencheur webhook
            # n'a pas d'échéance, mais s'il en recevait une par erreur (une
            # colonne posée à la main, un chemin d'écriture oublié), le tick le
            # ferait partir à l'horloge EN PLUS de l'événement. Le genre est la
            # garde ; l'échéance NULL n'est que la conséquence.
            f"WHERE enabled AND kind = 'schedule' AND next_due <= NOW() "
            f"ORDER BY next_due LIMIT %s",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


def consume_due(trigger_id: int, seen_next_due, new_next_due) -> bool:
    """Compare-and-swap sur l'échéance : True = CE tick a gagné et doit enfiler ;
    False = un tick concurrent (l'autre environnement, même base) l'a déjà fait.

    ⚠️ **Le verrou porte sur l'ÉLIGIBILITÉ (`next_due <= NOW()`), jamais sur
    l'échéance relue.** La version d'avant comparait `next_due = <valeur lue par
    le tick>` — et cette valeur passe par `_normalize_value`, qui **retire les
    microsecondes ET le fuseau** de tout horodatage lu.

    Deux façons pour ce `WHERE` de ne jamais matcher, aucune ne produisant
    d'erreur :

    ```
    microsecondes   une échéance à 19:37:27.482 est relue « 19:37:27 »
    fuseau retiré   la chaîne naïve est réinterprétée dans le fuseau de la
                    SESSION, pas forcément UTC
    ```

    Dans les deux cas `consume_due` rend `False`, que le tick lit comme « un pair
    a déjà consommé cette échéance » — le cas NORMAL quand deux environnements
    partagent la base. Il passe sans enfiler, **sans erreur, sans avertissement**,
    et le déclencheur reste **éternellement dû** : sélectionné à chaque tour,
    jamais consommé. ⚠️ Avec l'air parfaitement sain — `enabled`, une échéance
    dans le passé, un runner armé.

    Ça ne se produisait pas parce que toutes les échéances viennent de croniter,
    qui rend des secondes rondes. **Une garantie qui tient par la propriété d'une
    bibliothèque tierce n'est pas une garantie.**

    L'exclusion mutuelle est intacte : deux ticks concurrents se sérialisent sur
    la ligne, et le second ré-évalue son `WHERE` après le verrou — l'échéance est
    alors dans le futur, il ne matche plus. `seen_next_due` n'est plus lu ; il
    reste dans la signature pour ne pas casser les appelants, et parce que le
    perdre effacerait la trace de ce qu'on a corrigé.
    """
    with _connect() as conn:
        cur = conn.execute(
            """
            UPDATE runner_triggers
               SET next_due = %s, last_enqueued_at = NOW()
             WHERE id = %s AND enabled AND next_due <= NOW()
            """,
            (new_next_due, trigger_id),
        )
        return bool(cur.rowcount)
