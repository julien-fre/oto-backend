"""AJOUTER une ligne dont la clé existe déjà ne la FUSIONNE plus en silence (oto#141) —
le préavis daté.

Quand un tableau déclare une clé métier, une écriture SANS `id` dont la valeur de clé
existait déjà mettait la ligne en place à jour en silence — ligne seule, lot, REST,
upload signé. Et dans un lot, deux lignes à la même clé fusionnaient entre elles :
`ids: [r1, r1, r2, r3]` pour trois lignes écrites.

Arbitré le 30/09/2026, précisé le 01/10 : tout dépend de ce que l'appel DIT de la clé.

- **Désigner** — par `id=`, ou par `key=` passé EXPLICITEMENT à l'appel (lot, ligne seule
  qui nomme la clé déclarée, clé scellée au mint d'un upload), ou sur un tableau FERMÉ
  (`new_rows: "reject"`, où une écriture sans `id` ne peut que viser) : une valeur de clé déjà
  présente MODIFIE sa ligne, sans `upsert` et sans un mot ; une valeur absente crée la
  ligne (sauf tableau fermé, dont le refus de création s'applique).
- **Ajouter** — ni `id` ni `key=`, sur un tableau qui déclare une clé : une valeur déjà
  présente est un doublon que l'appelant n'a pas vu. Avant la date : la ligne fusionne
  et `notices` l'avertit, daté ; à partir d'elle : REFUS `business_key_exists`, qui nomme
  la ligne en place — sauf `upsert=true`, qui demande la fusion.
- **Deux lignes du même appel à la même clé** : on ne désigne pas deux fois la même
  ligne dans un appel. Sans `upsert`, avertie avant la date, le lot REFUSÉ ENTIER à
  partir d'elle — en désignation comme en ajout.

Un lot rend chaque fusion dans `fusions` — `{rang, dans_rang, id, cle}`, `dans_rang` =
le rang de la ligne du MÊME geste qui a posé la ligne visée, `null` pour une ligne déjà
en base —, et `ids` reste aligné rang pour rang.

⚠️ **La règle de comparaison n'est PAS ici.** Qu'une valeur « existe déjà », c'est
`db.datastore_find_row_id_by_key` qui le dit, et rien d'autre. Deux lignes d'un même lot
« à la même clé » se jugent sur le texte que la BASE rend de leur valeur déballée
(`db.datastore_textes_de_cle`, la conversion du lookup, oto#223) — jamais sur une
conversion Python.

**Le refus tombe À LA DATE, dans le code** : rien n'est à déployer le jour J. Le réglage
`OTO_UPSERT_IMPLICITE_REFUSE_LE` déplace la date sans déployer.
"""
from __future__ import annotations

import os
from datetime import date as _date
from typing import Any, Callable, Iterable, Optional

from . import schema as dsv2
from .errors import BusinessKeyExists

#: La date à partir de laquelle la fusion implicite est REFUSÉE. Une seule constante :
#: le texte servi en est DÉRIVÉ, pour que ce qu'on annonce soit ce qu'on refusera.
UPSERT_IMPLICITE_REFUSE_LE = _date(2026, 10, 21)

#: Déplace la date sans déployer (`YYYY-MM-DD`). Une valeur illisible LÈVE, elle ne
#: retombe pas sur le défaut (cf. `champs_reserves.date_reglee`).
ENV_UPSERT_IMPLICITE_REFUSE_LE = "OTO_UPSERT_IMPLICITE_REFUSE_LE"

#: Au-delà, un refus de lot cite les premiers cas et compte les autres.
_CITES = 5


def date_du_refus() -> _date:
    """La date en vigueur : le réglage s'il est posé, le défaut du code sinon."""
    from .champs_reserves import date_reglee

    return date_reglee(ENV_UPSERT_IMPLICITE_REFUSE_LE,
                       os.environ.get(ENV_UPSERT_IMPLICITE_REFUSE_LE),
                       UPSERT_IMPLICITE_REFUSE_LE)


def _aujourdhui() -> _date:
    """Le jour qui juge le refus (UTC). Un point d'appui à part, pour que les bancs
    fixent le jour de CETTE bascule sans toucher à l'horloge ni aux autres."""
    from .champs_reserves import jour_utc

    return jour_utc()


def refus_arme(aujourdhui: Optional[_date] = None) -> bool:
    """La fusion implicite est-elle refusée, AUJOURD'HUI (jour UTC) ?"""
    return (aujourdhui or _aujourdhui()) >= date_du_refus()


def _quand() -> str:
    from .champs_reserves import _en_francais

    return _en_francais(date_du_refus())


# ── le texte servi ───────────────────────────────────────────────────────────

_GESTES_EN = ("To DESIGNATE a row, pass its `id=`, or name the business key with `key=` "
              "(a batch, or a single row naming the declared key): an existing key value "
              "then MODIFIES its row and a new one creates it, no `upsert` needed. Without "
              "`id` or `key=`, a write ADDS a row.")

_FUSIONS_EN = ("A batch reports every merge in `fusions` — `{rang, dans_rang, id, cle}`, "
               "`dans_rang` being the earlier row of the SAME batch that made the target "
               "row, `null` for a row already in the table — and `ids` stays aligned row "
               "for row.")

_FERME_EN = ("On a CLOSED table (`new_rows: \"reject\"`) every write without `id` designates "
             "by the key.")


def description_ecriture() -> str:
    """L'annonce servie dans la description de `data_write`, DÉRIVÉE de la date.

    Composée au démarrage : avant la date, le préavis — qui reste VRAI si le process
    franchit la date sans redémarrer (« from … on » et « until then » décrivent chacun
    leur côté) ; démarré après, la règle au présent."""
    if refus_arme():
        return ("⚠️ **Business key: ADDING a row whose key value already exists is "
                "REFUSED** (`business_key_exists`, naming the existing row) unless you "
                f"pass `upsert=true`, which merges onto it. {_GESTES_EN} Two rows of one "
                "call with the same key value refuse the WHOLE batch, before anything is "
                f"written, unless `upsert=true`. {_FUSIONS_EN} {_FERME_EN}")
    return (f"⚠️ **Business key: from {date_du_refus().isoformat()} on, ADDING a row "
            "whose key value already exists is REFUSED** (`business_key_exists`, naming "
            "the existing row) instead of merging silently, unless you pass "
            f"`upsert=true` (accepted now). {_GESTES_EN} From the same date, two rows of "
            "one call with the same key value refuse the WHOLE batch, before anything "
            "is written, unless `upsert=true`. Until then both still merge, and the "
            f"response warns in `notices`. {_FUSIONS_EN} {_FERME_EN}")


#: Composée une fois, au démarrage (cf. `description_ecriture`).
DESCRIPTION_ECRITURE = description_ecriture()


def description_cle_schema() -> str:
    """Ce que la clé métier DÉCLARÉE fait à l'écriture, pour `data_set_schema` et
    `data_patch_schema` — la phrase qui disait « EVERY write … then UPSERTs on it »."""
    quand = ("" if refus_arme() else
             f" from {date_du_refus().isoformat()} on (until then it merges, with a "
             "warning in `notices`)")
    return ("A write that DESIGNATES by the key — `key=` passed to the call (a batch, "
            "or a single row naming this key), or any write without `id` on a CLOSED "
            "table — modifies the row of an existing key value and creates a new one. A "
            "write that ADDS (neither `id` nor `key=`) with a key value already present "
            f"is REFUSED{quand} unless it passes `upsert=true`, which merges onto that "
            "row — `data_write`, REST and `oto_upload_url` alike. The key never "
            "duplicates: its UNIQUE index guarantees it.")


def description_parametre() -> str:
    """Le paramètre `upsert`, tel que le servent les faces (MCP, REST, upload)."""
    sans = ("is REFUSED (`business_key_exists`)" if refus_arme() else
            f"still merges until {date_du_refus().isoformat()}, with a warning in "
            "`notices`, and is REFUSED from that date on")
    return ("`true` = MERGE when the key value already exists — for a write that ADDS "
            "(neither `id` nor `key=`), and for rows of one call that share a key value. "
            "A write that designates by `key=` needs no `upsert`. Every merge of a batch "
            f"comes back in `fusions`. Without it, an adding write on an existing key, "
            f"or two rows of one call on the same key, {sans}. Refused on a table with "
            "no business key: there is nothing to merge on.")


# ── ce que l'écriture en fait ─────────────────────────────────────────────────

def designe(schema: Optional[dict], cle_passee: bool) -> bool:
    """L'appel DÉSIGNE-t-il par la clé ? `key=` passé explicitement, ou un tableau FERMÉ
    — qui ne crée jamais, donc où une écriture sans `id` ne peut que viser (son refus de
    création le conseille, `outils._refus_de_creation`)."""
    return bool(cle_passee) or dsv2.creation_refusee(schema)


def valeur_de_cle(data: Any, key: Optional[str]) -> Any:
    """La valeur de clé qu'un lot CHERCHE pour cette ligne, ou None — déballée (une clé
    annotée est la même identité qu'une clé nue), et vide = aucune (cf. `lots.py`)."""
    if not key or not isinstance(data, dict):
        return None
    kv = dsv2.unwrap(data.get(key))
    return None if kv is None or str(kv) == "" else kv


def refuser_upsert_sans_cle(upsert: bool, key: Optional[str], datastore: str, *,
                            lot: bool = False) -> None:
    """`upsert=true` sans clé en vigueur ne fusionnerait RIEN, et l'appelant croirait
    avoir dédoublonné : refusé, comme tout paramètre offert qui ne serait pas réglé."""
    if upsert and not key:
        raise ValueError(
            f"`upsert=true` n'a rien sur quoi fusionner : `{datastore}` ne déclare pas de "
            f"clé métier{', et ce lot ne passe pas `key=`' if lot else ''}. Rien n'a été "
            "écrit. Déclare-la (data_patch_schema(key=…))"
            f"{' ou passe `key=` au lot' if lot else ''} ; sinon retire `upsert`.")


def _geste(key: str, row_id: Optional[str] = None) -> str:
    cible = f"`id={row_id}`" if row_id else "son `id`"
    return (f"Pour MODIFIER la ligne en place, désigne-la : {cible}, ou `key={key!r}` ; "
            "pour fusionner un ajout, passe `upsert=true`.")


def refus_unitaire(datastore: str, key: str, kv: Any, row_id: str, *,
                   nommer: bool = True) -> BusinessKeyExists:
    """Le refus d'UN ajout : la valeur, la ligne qu'elle désigne, les gestes.
    `nommer=False` : lu par un porteur de lien anonyme (upload signé) — l'identifiant
    interne de la ligne ne sort pas (oto#86)."""
    ligne = f"la ligne `{row_id}`" if nommer else "une ligne"
    return BusinessKeyExists(
        f"cette écriture AJOUTE une ligne (ni `id` ni `key=`), mais `{key}` = "
        f"{str(kv)!r} désigne déjà {ligne} de `{datastore}` : rien n'a été écrit pour "
        f"elle. Depuis le {_quand()}, un ajout ne fusionne plus de lui-même sur la clé "
        f"métier. {_geste(key, row_id if nommer else None)}",
        key=key, details={"key": key, "valeur": kv,
                          **({"id": row_id} if nommer else {})})


def refus_de_doublon(datastore: str, key: str, kv: Any, rangs: list[int]) -> BusinessKeyExists:
    """Le refus d'une ligne qui retrouve, PENDANT le lot, une ligne du même lot — une
    course, le lot ayant été jugé entier avant sa première ligne."""
    return BusinessKeyExists(
        f"lignes {_rangs(rangs)} de cet appel : même {key} {str(kv)!r} — on ne désigne "
        f"pas deux fois la même ligne dans un appel. Depuis le {_quand()}, c'est refusé "
        "sans `upsert=true`. Retire le doublon du lot, ou passe `upsert=true` pour "
        "fusionner.", key=key,
        details={"key": key, "doublons": [{"rangs": rangs, "valeur": kv}],
                 "existantes": []})


def notice_unitaire(key: str, kv: Any, row_id: str) -> str:
    return (f"À partir du {_quand()}, cette écriture sera refusée : elle AJOUTE une "
            f"ligne (ni `id` ni `key=`) et `{key}` = {str(kv)!r} désigne déjà la ligne "
            f"`{row_id}` — elle y a fusionné. {_geste(key, row_id)}")


def notice_lot(key: str) -> str:
    """Une phrase par CLÉ et non par ligne : un import de huit mille lignes ne rend pas
    huit mille avertissements, et un import découpé en tranches en rend un seul."""
    return (f"À partir du {_quand()}, ce lot sera refusé : des lignes AJOUTÉES (ni `id` "
            f"ni `key=`) ont fusionné sur la clé métier `{key}` sans `upsert=true` — "
            "leurs rangs sont dans `fusions` (`dans_rang: null`). Pour modifier des "
            f"lignes en place, désigne-les : `key={key!r}` ; pour fusionner, passe "
            "`upsert=true`.")


def notice_doublon(key: str) -> str:
    return (f"À partir du {_quand()}, ce lot sera refusé : des lignes du même appel "
            f"portent la même valeur de `{key}` et ont fusionné entre elles — leurs rangs "
            "sont dans `fusions` (`dans_rang` renseigné). On ne désigne pas deux fois la "
            "même ligne dans un appel : retire le doublon, ou passe `upsert=true`.")


def controler_fusion(notices: set, *, designation: bool, upsert: bool,
                     datastore: str, key: str, kv: Any, row_id: str,
                     dans_rang: Optional[int] = None, rang: Optional[int] = None,
                     lot: bool = False, nommer: bool = True) -> None:
    """Une écriture sans `id` s'apprête à fusionner dans `row_id` par sa clé.

    `upsert` : demandé, rien à dire. `dans_rang` renseigné : la ligne visée vient d'être
    posée par le MÊME appel — un doublon, en désignation comme en ajout. Sinon la ligne
    était en base : une `designation` la modifie sans un mot, un AJOUT est averti avant
    la date et refusé à partir d'elle. Sur un lot, seule une COURSE arrive encore ici
    après la date, le lot ayant été jugé entier avant sa première ligne."""
    if upsert:
        return
    if dans_rang is not None:
        if refus_arme():
            raise refus_de_doublon(datastore, key, kv, [dans_rang, rang])
        notices.add(notice_doublon(key))
        return
    if designation:
        return
    if refus_arme():
        raise refus_unitaire(datastore, key, kv, row_id, nommer=nommer)
    notices.add(notice_lot(key) if lot else notice_unitaire(key, kv, row_id))


def _rangs(rangs: list[int]) -> str:
    tete = ", ".join(str(r) for r in rangs[:-1])
    return f"{tete} et {rangs[-1]}"


def _majuscule(texte: str) -> str:
    return texte[:1].upper() + texte[1:]


def _cites(phrases: list[str]) -> str:
    reste = len(phrases) - _CITES
    return " ".join(phrases[:_CITES]) + (f" … et {reste} autre(s)." if reste > 0 else "")


def juger_le_lot(rows: Iterable[Any], *, key: str, datastore: str, designation: bool,
                 chercher: Callable[[Any], Optional[str]],
                 textes: Callable[[list], list[str]], decalage: int = 0,
                 nommer: bool = True) -> None:
    """Sans `upsert`, à partir de la date, le lot ENTIER est jugé avant sa première
    écriture : ses doublons internes toujours, et — s'il AJOUTE (`designation` fausse) —
    ses lignes dont la clé est déjà en base. Un refus ici n'a rien écrit — le lot n'étant
    pas atomique, le juger ligne à ligne laisserait sa première moitié écrite.

    `chercher(valeur)` = le lookup de clé du store (`db.datastore_find_row_id_by_key`),
    appelé une fois par valeur distincte ; `textes(valeurs)` = le texte que la base
    rend de chaque valeur (`db.datastore_textes_de_cle`), sur lequel deux lignes du lot
    sont « la même clé ». `decalage` = le rang absolu de la ligne qui
    précède ce lot (import découpé en tranches). `nommer=False` : l'identifiant interne
    des lignes en place ne sort pas (porteur de lien anonyme, oto#86)."""
    premiers: dict[str, int] = {}
    doublons: dict[str, list[int]] = {}
    valeurs: dict[str, Any] = {}
    existantes: list[dict] = []
    portees = [(decalage + i, kv) for i, kv in
               enumerate((valeur_de_cle(d, key) for d in rows), 1) if kv is not None]
    for (rang, kv), compare in zip(portees, textes([kv for _, kv in portees])):
        if compare in premiers:
            doublons.setdefault(compare, [premiers[compare]]).append(rang)
            continue
        premiers[compare], valeurs[compare] = rang, kv
        if designation:
            continue
        row_id = chercher(kv)
        if row_id is not None:
            existantes.append({"rang": rang, "valeur": kv,
                               **({"id": row_id} if nommer else {})})
    if not doublons and not existantes:
        return
    phrases = [f"Lignes {_rangs(r)} : même {key} {str(valeurs[c])!r}."
               for c, r in doublons.items()]
    phrases += [f"Ligne {e['rang']} : {key} {str(e['valeur'])!r} désigne déjà "
                + (f"la ligne `{e['id']}`." if nommer else "une ligne du tableau.")
                for e in existantes]
    regles = []
    if doublons:
        regles.append("on ne désigne pas deux fois la même ligne dans un appel")
    if existantes:
        regles.append("un AJOUT (ni `id` ni `key=`) ne fusionne plus de lui-même sur la "
                      "clé métier")
    gestes = (["un doublon se retire du lot"] if doublons else []) + (
        [f"pour modifier les lignes en place, désigne-les : `key={key!r}`"]
        if existantes else []) + ["pour fusionner, passe `upsert=true`"]
    raise BusinessKeyExists(
        f"lot refusé ENTIER, rien n'a été écrit : depuis le {_quand()}, "
        f"{' ; '.join(regles)} (`{key}` de `{datastore}`). {_cites(phrases)} "
        f"{_majuscule(' ; '.join(gestes))}.",
        key=key,
        details={"key": key,
                 "doublons": [{"rangs": r, "valeur": valeurs[c]}
                              for c, r in doublons.items()],
                 "existantes": existantes})


class Fusions:
    """Le relevé des fusions d'UN geste — un lot, ou un import découpé en tranches qui
    partagent le même relevé pour que `dans_rang` traverse les tranches.

    `decalage` = rang absolu de la ligne qui précède la tranche en cours ; `juge` = le
    geste a déjà été jugé ENTIER (`juger_le_lot`) par qui le découpe ; `nommer=False` =
    lu par un porteur de lien ANONYME (l'accusé d'un upload signé) : ni `fusions` ni
    les refus n'y portent l'identifiant interne d'une ligne (oto#86)."""

    def __init__(self, *, nommer: bool = True) -> None:
        self.decalage = 0
        self.juge = False
        self.nommer = nommer
        self._premier_rang: dict[str, int] = {}

    def creee(self, rang: int, row_id: str) -> None:
        self._premier_rang.setdefault(row_id, self.decalage + rang)

    def fusion(self, rang: int, row_id: str, colonne: str, valeur: Any) -> dict:
        """L'entrée de `fusions` : `dans_rang` = la ligne du même geste qui a posé (ou
        déjà visé) `row_id`, `None` si elle était en base avant lui."""
        absolu = self.decalage + rang
        entree = {"rang": absolu, "dans_rang": self._premier_rang.get(row_id),
                  **({"id": row_id} if self.nommer else {}), "cle": {colonne: valeur}}
        self._premier_rang.setdefault(row_id, absolu)
        return entree
