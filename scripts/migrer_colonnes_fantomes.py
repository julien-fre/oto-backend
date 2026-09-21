#!/usr/bin/env python3
"""Migre une colonne fantôme `<champ>_<couche>` (tiret bas, littérale) vers sa
couche imbriquée `<champ>.<couche>` (oto-backend#957, scission de #687).

⚠️ **La base est PARTAGÉE entre préproduction et production.** Ce script écrit des
DONNÉES DE CLIENTS. Il se lance À LA MAIN, en ssh root, par un humain — jamais par un
agent. **À blanc par défaut** : sans `--apply`, il classe et rapporte, n'écrit rien.

## Procédure — dans cet ordre, sans en sauter une marche

    # 1. à blanc : lire le rapport, ligne par ligne s'il le faut
    ssh -i ~/.ssh/<clé> root@<box> \
      "cd /opt/oto-mcp && ./.venv/bin/python -m scripts.migrer_colonnes_fantomes \
        <ns_id> <champ> <couche>"
    # 2+3. sauvegarde PUIS exécution — un seul geste : la sauvegarde de l'état
    #      antérieur est écrite (et relue) AVANT le premier UPDATE ; sans
    #      `--sauvegarde <répertoire>`, `--apply` est refusé
    ... <ns_id> <champ> <couche> --apply --sauvegarde /opt/oto-mcp/sauvegardes \
        [--purger-vides] [--assumer-provenance-non-datee]
    # 4. vérification : le rapport final relit le tableau et dit ce qui porte
    #    encore la clé fantôme ; un second passage à blanc doit trouver 0 ligne à
    #    migrer. En cas de doute, restaurer :
    ... --restaurer /opt/oto-mcp/sauvegardes/<fichier>.jsonl            # à blanc
    ... --restaurer /opt/oto-mcp/sauvegardes/<fichier>.jsonl --apply    # écrit

**Pourquoi ce script existe.** Avant le correctif de #687 (`oto_mcp/datastore/
points.py`), des agents écrivaient `effectif_comment` (colonne littérale, tiret bas)
là où il fallait `effectif.comment` (couche imbriquée). `points.py` referme l'aller-
retour export→import pour les écritures À VENIR ; les lignes déjà corrompues restent en
l'état — c'est ce que ce script nettoie, à la demande (« ni urgent ni bloquant »).

**Précondition, non négociable.** Ne JAMAIS le lancer sur un tableau où une porte
d'écriture pose encore des colonnes fantômes : la migration se déferait toute seule au
prochain écrit, sans que personne ne le remarque.

## Ce qui n'est PAS un fantôme — exclu, et dit

- **`<champ>_<couche>` DÉCLARÉE au schéma du tableau** : c'est une vraie colonne. Le
  script refuse tout le tableau et le dit.
- **Ligne sans `<champ>`** : `points.traduire_les_entetes` fabrique LÉGITIMEMENT
  `x_comment` depuis un en-tête CSV `x.comment` quand `x` n'est ni un en-tête ni au
  schéma. Rien ne consigne cette traduction : une ligne qui porte `x_comment` sans `x`
  peut en venir. Mise à part, listée, jamais touchée.
- **Socle opaque** : `<champ>` porte un objet qui n'est PAS fait de couches (juge
  unique : `couches.names_layers`) et la colonne n'est pas déclarée `json`. L'annoter
  changerait sa forme stockée — le même refus que `points.py` à l'écriture. Mis à part.

## Les populations et leur geste

    | fantôme vide ? | couche déjà posée ?    | population               | geste                 |
    |----------------|------------------------|--------------------------|-----------------------|
    | oui            | —                      | 3 — fantôme vide         | retiré (--purger-vides)|
    | non            | non (vide/absente)     | 1 — couche vide          | MIGRER                |
    | non            | oui, MÊME valeur       | 2 égale — redondant      | fantôme retiré        |
    | non            | oui, valeur DIFFÉRENTE | 2 conflit                | arbitrage humain      |

« Même valeur » au sens de `couches.same_value` (au type près). Le vide au sens de
`couches.est_vide`. Aucune de ces deux notions n'est réécrite ici.

## ⚠️ `comment`/`link` (VALUE_BOUND_LAYERS) : une provenance NON DATÉE

Ces couches décrivent LA VALEUR et tombent quand elle est réécrite. Si la valeur a été
réécrite APRÈS la pose du fantôme, migrer accrocherait une provenance périmée. **Les
données ne permettent pas de le savoir** : le fantôme et la valeur vivent dans la même
cellule JSONB, sans date par clé ; `updated_at` et `rev` sont ceux de la LIGNE (toute
écriture de n'importe quelle colonne les avance) ; le journal `tool_calls` ne garde que
des NOMS de champs (jamais les valeurs), borné à 50 champs, sans ligne visée pour une
écriture en lot, écrit au mieux (best-effort) et archivé puis purgé après 30 jours
(`deploy/archive_tool_calls.py`) — or les fantômes datent d'avant le 14/09. L'absence
de trace n'y prouve donc rien. Le script ne tranche pas cette doctrine : sur `comment`
et `link`, la population 1 est **mise à part** (listée, non migrée) tant que l'humain
n'a pas passé `--assumer-provenance-non-datee`. `origine` n'est pas concernée : elle
décrit le point de départ, pas la valeur courante.

## Écriture : jamais par-dessus ce qu'un autre vient d'écrire

Chaque ligne est relue SOUS VERROU (`SELECT … FOR UPDATE`, `lock_timeout` court) dans
la transaction qui l'écrit. Si `<champ>` ou le fantôme ont bougé depuis l'inventaire,
la ligne est SAUTÉE et comptée — jamais réécrite depuis une lecture périmée. Une ligne
dont le verrou ne vient pas à temps est sautée aussi. `updated_at` est avancé (`rev`
l'est par son déclencheur).

## Sauvegarde et restauration

Avant le premier UPDATE, l'état antérieur de chaque ligne à écrire (`<champ>` et le
fantôme, tels que lus) est écrit dans un JSONL horodaté, synchronisé sur disque puis
relu. `--restaurer <fichier>` le rejoue, ligne par ligne sous verrou, et seulement là où
l'état est encore celui que la migration a laissé — une ligne modifiée depuis est
sautée et listée, jamais écrasée.

Idempotent : un second passage ne trouve plus rien à migrer."""
from __future__ import annotations

import argparse
import datetime
import json
import os
import pathlib
import sys
from typing import Any, Optional

import psycopg
from psycopg.types.json import Jsonb

from oto_mcp.datastore.couches import (LAYER_KEYS, VALUE_BOUND_LAYERS, VALUE_LAYER,
                                       est_vide, names_layers, same_value)
from oto_mcp.datastore.declaration import champ_declare
from oto_mcp.datastore.points import _socle_accueille_une_couche
from oto_mcp.db._conn import _connect_autocommit

#: Attente maximale d'un verrou de ligne. Court : une ligne tenue par un agent est
#: sautée et comptée, on ne gèle pas la production derrière elle.
LOCK_TIMEOUT = "2s"

MIGRER = "migrer"                  # population 1 : le fantôme devient la couche
RETIRER = "retirer_fantome"        # population 2 égale / 3 : seul le fantôme part


class Refus(Exception):
    """Un refus AVANT toute écriture — le message dit pourquoi et quoi faire."""


def _empreinte(v: Any) -> str:
    """Identité stricte d'une valeur JSON lue (`1` ≠ `1.0`, ordre des clés neutre)."""
    return json.dumps(v, sort_keys=True, ensure_ascii=False)


def _schema(conn, ns_id: int) -> Optional[dict]:
    r = conn.execute("SELECT schema FROM user_datastores WHERE id = %s",
                     (ns_id,)).fetchone()
    if r is None:
        raise Refus(f"tableau {ns_id} introuvable.")
    return r["schema"]


def _inventaire(ns_id: int, champ: str, fantome_key: str) -> list[dict]:
    """Une entrée par ligne qui porte la clé fantôme — lue en JSONB typé (`->`, jamais
    `->>` qui rendrait `5` en `"5"`), jamais déduite d'un texte servi (#680)."""
    with _connect_autocommit() as conn:
        rows = conn.execute(
            "SELECT row_id, (data ? %(champ)s) AS present, data->%(champ)s AS socle, "
            "       data->%(fk)s AS fantome "
            "  FROM datastore_rows "
            " WHERE ns_id = %(ns)s AND data ? %(fk)s ORDER BY row_id",
            {"champ": champ, "fk": fantome_key, "ns": ns_id}).fetchall()
    return [dict(r) for r in rows]


def _couche_actuelle(socle: Any, couche: str) -> Any:
    return socle.get(couche) if names_layers(socle) else None


def _socle_avec_couche(socle: Any, couche: str, valeur: Any) -> dict:
    """Même composition que `points.ranger_les_couches` : un socle à couches garde
    les siennes, tout autre devient la `valeur`."""
    return ({**socle, couche: valeur} if names_layers(socle)
            else {VALUE_LAYER: socle, couche: valeur})


def classer(rows: list[dict], couche: str, decl_champ: Optional[dict]) -> dict:
    """Range chaque ligne dans UNE catégorie. Pur : aucune lecture, aucune écriture."""
    json_declare = bool(decl_champ) and decl_champ.get("type") == "json"
    out = {k: [] for k in ("pop1", "pop2_egale", "pop2_conflit", "pop3",
                           "sans_champ", "socle_opaque")}
    for r in rows:
        fantome, socle = r["fantome"], r["socle"]
        if not r["present"]:
            out["sans_champ"].append(r)
        elif est_vide(fantome):
            out["pop3"].append(r)
        elif not (json_declare or _socle_accueille_une_couche(socle)):
            out["socle_opaque"].append(r)
        else:
            actuelle = _couche_actuelle(socle, couche)
            if est_vide(actuelle):
                out["pop1"].append(r)
            elif same_value(actuelle, fantome):
                out["pop2_egale"].append(r)
            else:
                out["pop2_conflit"].append(r)
    return out


def _ecrire_sauvegarde(repertoire: str, ns_id: int, champ: str, couche: str,
                       plan: list[tuple[str, dict]]) -> pathlib.Path:
    rep = pathlib.Path(repertoire)
    rep.mkdir(parents=True, exist_ok=True)
    quand = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    f = rep / f"colonnes-fantomes-ns{ns_id}-{champ}_{couche}-{quand}.jsonl"
    fk = f"{champ}_{couche}"
    with open(f, "x", encoding="utf-8") as fh:
        for geste, r in plan:
            fh.write(json.dumps({
                "ns_id": ns_id, "row_id": r["row_id"], "champ": champ,
                "couche": couche, "fantome_key": fk, "geste": geste,
                "socle_avant": r["socle"], "fantome_avant": r["fantome"],
                "socle_apres": (_socle_avec_couche(r["socle"], couche, r["fantome"])
                                if geste == MIGRER else r["socle"]),
            }, ensure_ascii=False) + "\n")
        fh.flush()
        os.fsync(fh.fileno())
    relues = sum(1 for ligne in f.read_text(encoding="utf-8").splitlines()
                 if json.loads(ligne))
    if relues != len(plan):
        raise Refus(f"sauvegarde {f} illisible ({relues}/{len(plan)} lignes relues) "
                    f"— rien n'a été écrit en base.")
    return f


def _sous_verrou(conn, ns_id: int, row_id: str, champ: str, fk: str) -> Optional[dict]:
    conn.execute(f"SET LOCAL lock_timeout = '{LOCK_TIMEOUT}'")
    return conn.execute(
        "SELECT (data ? %(champ)s) AS present, data->%(champ)s AS socle, "
        "       (data ? %(fk)s) AS fk_present, data->%(fk)s AS fantome "
        "  FROM datastore_rows WHERE ns_id = %(ns)s AND row_id = %(rid)s FOR UPDATE",
        {"champ": champ, "fk": fk, "ns": ns_id, "rid": row_id}).fetchone()


def _appliquer_ligne(conn, ns_id: int, champ: str, couche: str, geste: str,
                     r: dict) -> str:
    """Rend `ecrite`, `bougee` ou `verrou`. Lecture et écriture dans UNE transaction."""
    fk = f"{champ}_{couche}"
    try:
        with conn.transaction():
            cur = _sous_verrou(conn, ns_id, r["row_id"], champ, fk)
            if (cur is None or not cur["fk_present"]
                    or cur["present"] != r["present"]
                    or _empreinte(cur["socle"]) != _empreinte(r["socle"])
                    or _empreinte(cur["fantome"]) != _empreinte(r["fantome"])):
                return "bougee"
            if geste == MIGRER:
                conn.execute(
                    "UPDATE datastore_rows SET data = (data - %(fk)s) "
                    "       || jsonb_build_object(%(champ)s::text, %(socle)s::jsonb), "
                    "       updated_at = NOW() "
                    " WHERE ns_id = %(ns)s AND row_id = %(rid)s",
                    {"fk": fk, "champ": champ, "ns": ns_id, "rid": r["row_id"],
                     "socle": Jsonb(_socle_avec_couche(cur["socle"], couche,
                                                       cur["fantome"]))})
            else:
                conn.execute(
                    "UPDATE datastore_rows SET data = data - %(fk)s, updated_at = NOW() "
                    " WHERE ns_id = %(ns)s AND row_id = %(rid)s",
                    {"fk": fk, "ns": ns_id, "rid": r["row_id"]})
            return "ecrite"
    except psycopg.errors.LockNotAvailable:
        return "verrou"


def executer(ns_id: int, champ: str, couche: str, *, apply: bool = False,
             purger_vides: bool = False, sauvegarde: Optional[str] = None,
             assumer_provenance_non_datee: bool = False) -> dict:
    """Classe, puis (avec `apply`) sauvegarde et écrit. Rend le rapport — les
    `row_id` de chaque catégorie, ce qui a été écrit et ce qui a été sauté.
    Lève `Refus` avant toute écriture quand le geste n'est pas sûr."""
    if couche not in LAYER_KEYS:
        raise Refus(f"couche `{couche}` inconnue — attendu l'une de {LAYER_KEYS}.")
    if apply and not sauvegarde:
        raise Refus("--apply exige --sauvegarde <répertoire> : on n'écrit pas sur des "
                    "données de clients sans état antérieur rejouable.")
    fk = f"{champ}_{couche}"
    with _connect_autocommit() as conn:
        schema = _schema(conn, ns_id)
    if champ_declare(schema, fk):
        raise Refus(f"`{fk}` est DÉCLARÉE au schéma du tableau {ns_id} : c'est une "
                    f"vraie colonne, pas un fantôme. Rien n'a été touché.")

    rows = _inventaire(ns_id, champ, fk)
    pops = classer(rows, couche, champ_declare(schema, champ))
    ids = {k: [r["row_id"] for r in v] for k, v in pops.items()}
    liee = couche in VALUE_BOUND_LAYERS
    a_part = pops["pop1"] if liee and not assumer_provenance_non_datee else []
    plan = ([] if a_part else [(MIGRER, r) for r in pops["pop1"]])
    plan += [(RETIRER, r) for r in pops["pop2_egale"]]
    if purger_vides:
        plan += [(RETIRER, r) for r in pops["pop3"]]

    rapport = {"ns_id": ns_id, "fantome": fk, "inventaire": len(rows), **ids,
               "provenance_non_datee": [r["row_id"] for r in a_part],
               "conflits": [{"row_id": r["row_id"],
                             "couche": _couche_actuelle(r["socle"], couche),
                             "fantome": r["fantome"]} for r in pops["pop2_conflit"]],
               "apply": apply, "sauvegarde": None, "migrees": [],
               "fantomes_retires": [], "sautees_bougees": [], "sautees_verrou": [],
               "restantes": None}
    if not apply or not plan:
        return rapport

    rapport["sauvegarde"] = str(_ecrire_sauvegarde(sauvegarde, ns_id, champ, couche,
                                                   plan))
    with _connect_autocommit() as conn:
        for geste, r in plan:
            issue = _appliquer_ligne(conn, ns_id, champ, couche, geste, r)
            cle = {"bougee": "sautees_bougees", "verrou": "sautees_verrou"}.get(
                issue, "migrees" if geste == MIGRER else "fantomes_retires")
            rapport[cle].append(r["row_id"])
    rapport["restantes"] = [r["row_id"] for r in _inventaire(ns_id, champ, fk)]
    return rapport


def restaurer(fichier: str, *, apply: bool = False) -> dict:
    """Rejoue une sauvegarde. Ne restaure une ligne que si elle est ENCORE dans
    l'état que la migration a laissé ; sinon la saute et la liste."""
    entrees = [json.loads(ligne) for ligne in
               pathlib.Path(fichier).read_text(encoding="utf-8").splitlines() if ligne]
    rapport = {"fichier": fichier, "apply": apply, "restaurees": [],
               "a_restaurer": [], "non_migrees": [], "modifiees_depuis": [],
               "sautees_verrou": []}
    with _connect_autocommit() as conn:
        for e in entrees:
            ns_id, rid, champ, fk = e["ns_id"], e["row_id"], e["champ"], e["fantome_key"]
            try:
                with conn.transaction():
                    cur = _sous_verrou(conn, ns_id, rid, champ, fk)
                    if cur is not None and cur["fk_present"]:
                        rapport["non_migrees"].append(rid)
                        continue
                    if (cur is None or not cur["present"]
                            or _empreinte(cur["socle"]) != _empreinte(e["socle_apres"])):
                        rapport["modifiees_depuis"].append(rid)
                        continue
                    if not apply:
                        rapport["a_restaurer"].append(rid)
                        continue
                    conn.execute(
                        "UPDATE datastore_rows SET data = data "
                        "       || jsonb_build_object(%(champ)s::text, %(socle)s::jsonb, "
                        "                             %(fk)s::text, %(fantome)s::jsonb), "
                        "       updated_at = NOW() "
                        " WHERE ns_id = %(ns)s AND row_id = %(rid)s",
                        {"champ": champ, "fk": fk, "ns": ns_id, "rid": rid,
                         "socle": Jsonb(e["socle_avant"]),
                         "fantome": Jsonb(e["fantome_avant"])})
                    rapport["restaurees"].append(rid)
            except psycopg.errors.LockNotAvailable:
                rapport["sautees_verrou"].append(rid)
    return rapport


def _afficher(rapport: dict) -> None:
    for cle, val in rapport.items():
        if isinstance(val, list):
            print(f"  {cle} : {len(val)}")
            for v in val:
                print(f"      {v}")
        else:
            print(f"  {cle} : {val}")


def main(argv: Optional[list] = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("ns_id", type=int, nargs="?", help="identifiant du tableau")
    p.add_argument("champ", nargs="?", help="nom du champ (ex. effectif)")
    p.add_argument("couche", nargs="?", help="couche visée : origine, comment ou link")
    p.add_argument("--apply", action="store_true", help="écrit (sinon à blanc)")
    p.add_argument("--sauvegarde", help="répertoire du JSONL d'état antérieur "
                                        "(obligatoire avec --apply)")
    p.add_argument("--purger-vides", action="store_true",
                   help="retire aussi le fantôme VIDE (population 3)")
    p.add_argument("--assumer-provenance-non-datee", action="store_true",
                   help="comment/link : migre la population 1 bien que rien ne dise si "
                        "la valeur a été réécrite depuis la pose du fantôme")
    p.add_argument("--restaurer", metavar="FICHIER",
                   help="rejoue une sauvegarde (à blanc sans --apply)")
    a = p.parse_args(argv)
    try:
        if a.restaurer:
            rapport = restaurer(a.restaurer, apply=a.apply)
        else:
            if a.ns_id is None or not a.champ or not a.couche:
                p.error("ns_id, champ et couche sont requis (hors --restaurer)")
            rapport = executer(a.ns_id, a.champ, a.couche, apply=a.apply,
                               purger_vides=a.purger_vides, sauvegarde=a.sauvegarde,
                               assumer_provenance_non_datee=a.assumer_provenance_non_datee)
    except Refus as e:
        print(f"REFUS : {e}")
        return 2
    print("ÉCRIT" if a.apply else "À BLANC — rien n'a été écrit (--apply pour exécuter)")
    _afficher(rapport)
    return 0


if __name__ == "__main__":
    sys.exit(main())
