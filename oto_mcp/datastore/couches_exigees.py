"""Quelles couches une colonne EXIGE — `required_layers` (oto#75).

Une colonne peut demander qu'on ne pose pas sa valeur nue : `required_layers:
["comment"]` refuse une écriture qui n'explique pas d'où vient ce qu'elle affirme.
Ce module tient ce cran seul — ce qu'une déclaration exige (`required_layers_of`), le
relevé de ce qui manque (`couches_manquantes`, `_couches_exigees_errors`, et sa
descente dans les items d'une colonne-liste `_couches_exigees_sous`), et la phrase de
refus qui dit à QUOI SERT la couche réclamée (`_refus_de_couche`) plutôt que de la
nommer sèchement.

**Séparé de `validation.py` pour une raison de fond** : les autres refus jugent la
valeur (son type, sa longueur, sa présence) ; celui-ci juge ce qui l'ACCOMPAGNE. Il ne
descend pas par `_row_errors` mais par son propre chemin, et c'est ce qui le rend
lisible isolément — c'est aussi ce qui l'a laissé vivre trois schémas de production
sans lecteur avant qu'une sonde ne l'annonce.

Ce qu'il ne tient pas :
- **le vocabulaire des couches** et leurs formes → `couches.py` ;
- **le refus de la valeur elle-même** → `validation.py`, qui appelle
  `couches_manquantes` d'ici ;
- **la couche que la PLATEFORME pose** (`origine: "system"`) → `champs_reserves.py` ;
  elle est d'ailleurs exclue des couches exigibles, une exigence ne pouvant porter
  sur ce que l'appelant n'a pas le droit d'écrire.
"""
from __future__ import annotations

from typing import Any, Optional

from .couches import _is_empty, LAYER_KEYS, ORIGIN_LAYER, split_layer, SYSTEM_ORIGIN, unwrap
from . import charge_a_renvoyer as car
from .declaration import _fields, _walk_fields, cle_d_element
from .phrases_de_refus import gabarit

# ── les couches EXIGÉES (oto#75, barreau 1) ──────────────────────────────────
#
# `field.required_layers: ["comment"]` — une écriture qui laisse une valeur NON VIDE
# dans cette colonne sans y poser la couche est REFUSÉE, en nommant la colonne.
#
# ⚠️ **L'attribut existait déjà dans trois schémas de production et n'avait AUCUN
# lecteur** : posé, servi dans le contrat, et sans le moindre effet. Son auteur croyait
# la provenance exigée ; rien ne l'exigeait, et rien ne le disait. Ce lot lui donne son
# lecteur — la PRÉSENCE d'une couche, rien de plus : pas de motif sur le contenu (ce
# serait le barreau 2), pas de conditionnelle entre couches (le barreau 3).
#
# **Ce que cette garde ne fait pas, et il faut le dire ici :** elle n'empêche pas un
# commentaire FAUX. Elle oblige à NOMMER une source, ce qui rend le mensonge
# vérifiable ; la vérité reste à la relecture sur pièces.

#: Ce qu'une couche PORTE, en une clause — la moitié actionnable du refus.
_A_QUOI_SERT_LA_COUCHE = {
    "comment": "d'où vient la valeur, en clair",
    "link": "l'URL de la source",
    ORIGIN_LAYER: "ce qui tenait lieu de valeur avant",
}


def gabarit_de_couche(couche: str) -> str:
    """Ce que la charge à renvoyer met à la place d'une couche (oto#135) : ce qu'elle
    porte, entre chevrons — la même clause que le refus."""
    return f"<{_A_QUOI_SERT_LA_COUCHE.get(couche, 'texte')}>"


def required_layers_of(field: Any) -> tuple[str, ...]:
    """Les couches qu'une colonne EXIGE avec sa valeur, ou `()` — jamais d'exception.

    Même parti pris que `max_length_of` / `pattern_of` : une déclaration illisible se
    refuse à la POSE (`_validate_fields_def`) et reste MUETTE ici. Un vieux schéma
    déjà en base ne doit pas faire exploser une écriture — l'attribut dort dans des
    tableaux de production depuis avant ce lecteur.

    ⚠️ **`[]` n'est PAS une déclaration illisible** : c'est « aucune couche exigée »,
    et le `not declarees` ci-dessous en est le lecteur. La pose l'accepte donc aussi —
    les deux moitiés de l'attribut disent la même chose de la même valeur. Quatre
    tableaux vivants la portent ; une pose qui la refuserait les gèlerait.

    `origine` est retirée d'une colonne déclarée `origine: "system"` : là, la
    plateforme pose la couche elle-même et REFUSE que l'appelant la nomme
    (`reserved_refusals`). L'exiger rendrait la colonne inécrivable — un requis que
    la même plateforme interdit de satisfaire."""
    if not isinstance(field, dict):
        return ()
    declarees = field.get("required_layers")
    if not isinstance(declarees, (list, tuple)) or not declarees:
        return ()
    # ⚠️ **L'exemption `origine: "system"` est SUPPRIMÉE ici (08/09/2026).**
    # Elle retirait `origine` des couches exigées au motif que « la plateforme la pose
    # elle-même et refuse que l'appelant la nomme ». Elle ne la pose plus : le cran a
    # été remplacé par `donnees_d_origine`. L'exemption désarmait donc une exigence que
    # plus rien ne satisfaisait — une colonne pouvait déclarer `required_layers:
    # ["origine"]` et n'exiger rien du tout, en silence. Mesuré avant de retirer :
    # ZÉRO colonne du parc combine les deux, le piège vivait dans le code et pas dans
    # les données.
    return tuple(c for c in declarees if isinstance(c, str) and c in LAYER_KEYS)


def _refus_de_couche(fpath: str, cle: str, manquantes: tuple,
                     exigees: tuple) -> str:
    """Le refus, et surtout SA DESTINATION.

    Un refus qui dit seulement ce qui est interdit fait REJOUER le même appel : mesuré
    cette semaine sur la famille voisine, huit refus sur dix-huit revenaient à
    l'identique, certains en neuf secondes. Il faut donc la FORME à écrire, pas
    seulement la faute — et dans le MÊME appel, parce que poser la valeur seule au
    tour suivant emporterait la couche qu'on vient d'exiger (`_merge_column`)."""
    noms = ", ".join(f"`{c}`" for c in manquantes)
    couches = ", ".join(f'"{c}": "…"' for c in exigees)
    detail = " ; ".join(f"`{c}` = {_A_QUOI_SERT_LA_COUCHE[c]}"
                        for c in exigees if c in _A_QUOI_SERT_LA_COUCHE)
    return (
        f"{fpath}: valeur posée sans {noms} — cette colonne exige que la valeur "
        f"arrive AVEC sa provenance. Écris-la en couches, dans le MÊME appel : "
        f'`"{cle}": {{"valeur": <ta valeur>, {couches}}}` ({detail}). '
        f"Poser la valeur seule au tour suivant emporterait la couche. Une valeur "
        f"vide, ou une couche posée sans valeur, ne déclenche rien.")


def _couches_exigees_errors(fields: list, data: dict, path: str,
                            written: Optional[set] = None, *,
                            top: bool = True,
                            charge: Optional[dict] = None,
                            chemin: tuple = car.RACINE) -> list[str]:
    """Les refus de couche manquante d'un (sous-)record.

    `written` (premier niveau SEULEMENT) = les clés que le geste NOMME. La garde s'y
    restreint, exactement comme `max_length` et `pattern`, et pour la même raison :
    sans elle, un patch sur une AUTRE colonne serait refusé pour une couche absente
    ailleurs — le gel silencieux d'oto-backend#284, sur une règle de plus.

    ⚠️ La restriction se pose sur la colonne de BASE, jamais sur une clé pointée : une
    clé `colonne.comment` n'entre JAMAIS dans `written` (les noms pointés ne s'écrivent
    pas), donc une règle qui s'y restreindrait ne refuserait rien sur un patch —
    mesuré : c'est très exactement l'état de `pattern`/`max_length` posés sur une
    couche aujourd'hui, une déclaration acceptée et inerte hors insertion.

    `top=False` = on est DANS un composite que le geste réécrit. La fusion travaille
    au grain de la colonne (`_merge_column` n'est pas récursif) : réémettre une liste,
    c'est réémettre chacun de ses éléments, donc chaque sous-champ est posé par le
    geste. Il n'y a rien à restreindre plus bas — et restreindre par `written`, qui ne
    contient que des noms de tête, y refuserait TOUT.

    `charge`/`chemin` = la charge à renvoyer (oto#135) : la colonne fautive y reçoit sa
    valeur et les couches manquantes, en gabarits (`charge_a_renvoyer`)."""
    errors: list[str] = []
    for f in fields:
        if not isinstance(f, dict):
            continue
        key = f.get("key")
        if not isinstance(key, str) or not key:
            continue
        # Une cible de COUCHE (`colonne.comment`, #377) n'exige pas de couche : elle
        # EN EST une. `required_layers` y est refusée à la pose (`_COLUMN_ONLY_KEYS`).
        if split_layer(key)[1]:
            continue
        fpath = f"{path}.{key}" if path else key
        if top:
            if written is not None and key not in written:
                continue
            # Les exclusions de l'issue, et chacune ferme une colonne que l'appelant
            # n'écrit pas : `readonly` (la valeur vient du fichier source) et celle
            # qui porte le cycle de vie (un état n'est pas une observation, il se
            # justifie par sa transition). Exiger une provenance de qui n'écrit pas
            # la valeur ferait refuser des écritures que personne ne peut corriger.
            # `role: "status"` a disparu (08/09/2026) : l'état est la colonne qui
            # porte le `lifecycle`. L'exemption suit le bloc, pas l'étiquette.
            if f.get("readonly") is True or isinstance(f.get("lifecycle"), dict):
                continue
        brut = data.get(key)
        # Le VIDE ne déclenche rien — ni une valeur nulle, ni une couche posée seule
        # (c'est la forme légitime d'une remarque). `unwrap` rend None sur un dict qui
        # ne porte que des couches, donc les deux cas passent par le même test.
        if _is_empty(unwrap(brut)):
            continue
        exigees = required_layers_of(f)
        if exigees:
            posees = brut if isinstance(brut, dict) else {}
            manquantes = tuple(c for c in exigees if _is_empty(posees.get(c)))
            if manquantes:
                errors.append(_refus_de_couche(fpath, key, manquantes, exigees))
                car.noter(charge, car.champ(chemin, key),
                          {"valeur": gabarit(f),
                           **{c: gabarit_de_couche(c) for c in manquantes}})
        errors.extend(_couches_exigees_sous(f, unwrap(brut), fpath,
                                            charge=charge, chemin=car.champ(chemin, key)))
    return errors


def _couches_exigees_sous(field: dict, valeur: Any, path: str, *,
                          charge: Optional[dict] = None,
                          chemin: tuple = car.RACINE) -> list[str]:
    """La portée DESCEND dans les composites — l'issue l'exige sur les sous-champs
    d'une liste, et c'est là que la perte est la plus lourde : une liste réémise
    remplace l'ancienne EN BLOC, couches comprises.

    Un attribut fautif se nomme UNE fois pour toute la colonne, sur le premier élément
    qui le porte — même borne que le refus de sous-champ inconnu, et même raison : 300
    contacts diraient 300 fois la même chose, et un refus qu'on ne peut pas lire ne
    vaut pas mieux qu'un silence."""
    ftype = field.get("type")
    if ftype == "object" and isinstance(valeur, dict):
        sub = [x for x in (field.get("fields") or []) if isinstance(x, dict)]
        return _couches_exigees_errors(sub, valeur, path, None, top=False,
                                       charge=charge, chemin=chemin)
    if ftype == "list" and isinstance(valeur, list):
        of = field.get("of")
        sub = ([x for x in (of.get("fields") or []) if isinstance(x, dict)]
               if isinstance(of, dict) else [])
        if not sub:
            return []
        errors: list[str] = []
        vus: set = set()
        cle_id = cle_d_element(field)
        for i, item in enumerate(valeur):
            if not isinstance(item, dict):
                continue
            ident = unwrap(item.get(cle_id)) if cle_id else None
            ichemin = car.element(chemin, i, (cle_id, ident)
                                  if isinstance(ident, (str, int)) and ident != "" else None)
            for sf in sub:
                cle = sf.get("key")
                if not isinstance(cle, str) or cle in vus:
                    continue
                msgs = _couches_exigees_errors([sf], item, f"{path}[{i}]",
                                               None, top=False,
                                               charge=charge, chemin=ichemin)
                if msgs:
                    vus.add(cle)
                    errors.extend(msgs)
        return errors
    return []


def couches_manquantes(schema: Optional[dict], merged: dict, *,
                       written: Optional[set] = None,
                       charge: Optional[dict] = None) -> list[str]:
    """Les colonnes qui portent une valeur sans la couche que leur schéma exige.

    ⚠️ **Armée par sa PROPRE déclaration**, comme le cycle de vie et pour la même
    raison — jamais par `validation_active`. Celle-ci arme la validation ENTIÈRE
    (types, requis, composites fermés) : l'élargir ferait basculer dans ce régime, du
    jour au lendemain, les tableaux qui portent DÉJÀ l'attribut, sur des règles qu'ils
    n'ont jamais demandées. Déclarer `required_layers`, c'est demander qu'on fasse
    respecter `required_layers` — pas le reste."""
    if not isinstance(schema, dict):
        return []
    fields = _fields(schema)
    if not any(required_layers_of(f) for f in _walk_fields(fields)):
        return []
    return _couches_exigees_errors(fields, merged, "", written, charge=charge)
