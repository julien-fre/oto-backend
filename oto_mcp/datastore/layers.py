"""La forme IMBRIQUÉE d'une ligne servie — l'option `layers=nested` (oto#53).

Une colonne s'ÉCRIT imbriquée, `{"champ": {"valeur": …, "comment": …, "origine": …,
"link": …}}`, et se RELIT à plat : `champ` = la valeur seule, `champ.comment` à côté,
au premier niveau de la ligne (`schema.flat_layers`, appelée par `core._row_to_dict`).
Un client qui relit `row["champ"]` en attendant la forme qu'il a écrite conclut que sa
couche a disparu — elle n'a pas disparu, la forme a changé entre l'aller et le retour
(oto#47). Ici, la forme symétrique : une cellule à couches revient comme elle s'écrit.

Palier 1 d'une bascule en trois temps (oto#53) : l'option d'abord, le défaut reste
`flat` ; la mesure des consommateurs de la forme plate ensuite ; la bascule du défaut
enfin, avec préavis daté et double-service. **Le défaut se lit ici et nulle part
ailleurs** — les deux faces le prennent d'ici, pour qu'une bascule soit un seul geste.

Ce module ne fait QUE la mise en forme. Il ne connaît ni la base, ni le schéma : il
reçoit ce que `_row_to_dict` reçoit — la valeur stockée d'une colonne — et rend ce
qu'un lecteur `nested` doit voir. La règle du premier niveau s'applique un cran plus
bas, dans les items d'une colonne-liste, exactement comme `schema._served_item` le
fait pour la forme plate : qui sait lire `row["email"]["origine"]` sait lire
`item["email"]["origine"]`.
"""
from __future__ import annotations

from typing import Any

from . import couches as dsc
from . import schema as dsv2

FLAT = "flat"
NESTED = "nested"
FORMES = (FLAT, NESTED)
DEFAUT = FLAT  # ⚠️ palier 3 (oto#53) : bascule vers NESTED, avec préavis daté.

# ── `empties` : ce qu'un lecteur reçoit pour une case au vide ASSUMÉ (oto#204) ──
#
# **Le défaut que ça ferme.** Une liste sans `of.key` se remplace en bloc : relue puis
# réémise, la case au vide assumé revient `""`, un vide ORDINAIRE, et l'écriture est
# refusée (« champ requis manquant »). Sans perte, mais sans issue honnête : le client
# ne peut pas savoir que ce `""`-là était assumé — les deux vides sont servis pareil —,
# donc il invente une valeur, ou pose `@empty` sur un vide que personne n'a cherché.
#
# `sentinel` sert à ces cases le MOT qui les écrit, `"@empty"` : réémis tel quel, il
# repose le marqueur. Un `""` ordinaire reste `""`. Le marqueur, lui, n'est jamais
# servi sous son nom — le client écrit le geste, pas la clé interne.
#
# ⚠️ **Une forme DEMANDÉE, jamais le défaut.** `"@empty"` dans une page publique, un
# export ou un écran serait un texte métier littéral : `plain` reste la forme de toutes
# les surfaces qui ne réécrivent pas.
PLAIN = "plain"
SENTINEL = "sentinel"
EMPTIES = (PLAIN, SENTINEL)
EMPTIES_DEFAUT = PLAIN


def check_empties(value: Any) -> str:
    """La valeur d'`empties`, ou un refus qui NOMME le paramètre et les valeurs admises —
    même patron que `check`, pour la même raison. `None` vaut le défaut."""
    if value is None:
        return EMPTIES_DEFAUT
    if value in EMPTIES:
        return str(value)
    raise ValueError(
        f"`empties` inconnu : `{value}` — attendu `{PLAIN}` (défaut : une case vidée "
        f"délibérément est servie `\"\"`, comme un vide ordinaire) ou `{SENTINEL}` (servie "
        f"`\"{dsc.VIDE_DELIBERE}\"`, le mot qui la réécrit : la forme à lire avant de "
        f"renvoyer une liste).")


def relayer_empties(value: str) -> dict:
    """Ce qu'une face passe au store : `{"empties": …}` quand la forme est DEMANDÉE, `{}`
    au défaut.

    Le défaut appartient au store, pas à la face — même règle que le bail
    (`capabilities/datastore/claim._lease`). Un appel au défaut reste donc, pour tout ce
    qui l'observe, l'appel d'avant ce paramètre."""
    return {} if value == EMPTIES_DEFAUT else {"empties": value}


def check(value: Any) -> str:
    """La valeur de `layers`, ou un refus qui NOMME le paramètre, la valeur reçue et
    les deux formes admises — jamais un `invalid_input` nu qui oblige à deviner.

    `None` vaut le défaut : les deux faces passent leur paramètre tel quel, et une
    face qui n'a rien reçu ne doit pas avoir à connaître le défaut de l'autre."""
    if value is None:
        return DEFAUT
    if value in FORMES:
        return str(value)
    raise ValueError(
        f"`layers` inconnu : `{value}` — attendu `{FLAT}` (défaut : `champ` = la valeur, "
        f"`champ.origine`/`.comment`/`.link` à plat à côté) ou `{NESTED}` "
        f"(`champ` = {{\"valeur\", \"origine\", \"comment\", \"link\"}}, la forme écrite).")


def nested_value(value: Any, *, sentinelle: bool = False) -> Any:
    """Ce qu'un lecteur `nested` reçoit pour une colonne.

    - cellule à couches → `{"valeur": …, + chaque couche RENSEIGNÉE}` : `valeur` est
      toujours là (`None` quand la colonne porte une provenance sans valeur posée —
      l'import de socle), les couches seulement quand elles le sont, comme à plat
      (`flat_layers` tait `None`/`""`) ;
    - cellule sans couche → sa valeur, telle que la forme plate la sert (`unwrap`,
      puis la descente dans les items d'une liste).

    Les DEUX formes partent de la même valeur déballée : ce que `flat` sert sous le nom
    nu et ce que `nested` sert sous `valeur` est le même objet, par construction.

    `sentinelle` (`empties=sentinel`, oto#204) : une case au vide ASSUMÉ revient
    ENVELOPPÉE, `{"valeur": "@empty", …couches}` — même quand elle ne porte aucune autre
    couche. C'est la forme d'écriture de ce geste, et c'est ce que `nested` promet."""
    if sentinelle and dsc.vide_assume(value):
        out = {dsv2.VALUE_LAYER: dsc.VIDE_DELIBERE}
        for layer in dsv2.LAYER_KEYS:
            if value.get(layer) not in (None, ""):
                out[layer] = value[layer]
        return out
    if isinstance(value, dict) and any(k in dsv2.LAYER_KEYS for k in value):
        out: dict = {dsv2.VALUE_LAYER: _plain(dsv2.unwrap(value), sentinelle)}
        for layer in dsv2.LAYER_KEYS:
            if value.get(layer) not in (None, ""):
                out[layer] = value[layer]
        return out
    return _plain(dsv2.unwrap(value), sentinelle)


def _plain(v: Any, sentinelle: bool = False) -> Any:
    """La valeur déballée, descendue dans une liste de fiches : un item non-dict
    traverse tel quel (une liste de scalaires reste une liste de scalaires)."""
    if isinstance(v, list):
        return [({k: nested_value(x, sentinelle=sentinelle) for k, x in item.items()}
                 if isinstance(item, dict) else item) for item in v]
    return v
