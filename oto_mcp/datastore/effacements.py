"""Retoucher un format sans en perdre un morceau — fusion, retraits, et leur relevé.

Deux gestes distincts, et les confondre a coûté cinquante-deux notes de champ sur un
tableau de production :

- **fusionner** un patch dans le format en place (`merge_fields`, qui délègue le bloc
  de cycle de vie à `cycle_de_vie.merge_lifecycle`) — ajouter sans écraser ;
- **retirer** explicitement, colonne entière (`remove_fields`) ou attribut par attribut
  (`remove_field_attrs`) — le seul chemin par lequel une déclaration disparaît
  volontairement.

La seconde moitié est le TÉMOIN de ce que le geste a détruit : `declarations_effacees`
compare l'ancien format au nouveau et nomme ce qui n'y est plus, avec sa valeur ;
`declarations_effacees_report` en fait le relevé rendu à l'appelant. C'est ce qui
permet à une reconstruction silencieuse de ne plus l'être — l'avertissement arrive sur
un appel qui RÉUSSIT, ce que personne ne lit ; le relevé, lui, nomme les pièces.

Ce qu'il ne tient pas :
- **les gestes du store** (`set_schema` remplace, `patch_schema` retouche) et le choix
  entre eux → `schema_ops.py`, qui appelle ces fonctions ;
- **la validité du format obtenu** après fusion → `definition.py` ;
- **la grammaire du bloc `lifecycle`** fusionné → `cycle_de_vie.py`.
"""
from __future__ import annotations

from typing import Any, Optional

from .declaration import _fields
from .cycle_de_vie import merge_lifecycle

def merge_fields(current: list, patch: list) -> tuple[list, list[str], list[str]]:
    """Fusionne `patch` dans `current` PAR CLÉ → `(fields, ajoutés, modifiés)`.

    Un field déjà présent est COMPLÉTÉ (les propriétés fournies écrasent, les autres
    sont préservées) ; un field inconnu est ajouté À LA FIN. L'ordre existant ne
    bouge pas : il pilote le rendu (ADR 0032 §6), le déplacer serait un effet de
    bord invisible dans un geste qui prétend ne toucher qu'aux propriétés nommées.

    La fusion descend dans les composites DÉCLARÉS (`object.fields`, `list.of` et
    ses `fields`) : sans ça, patcher un sous-record détruirait ses sous-champs —
    le trou qu'on ferme, un cran plus bas."""
    out = [dict(f) for f in current if isinstance(f, dict)]
    by_key = {f.get("key"): f for f in out if f.get("key")}
    added: list[str] = []
    updated: list[str] = []
    for p in patch:
        if not isinstance(p, dict):
            continue
        key = p.get("key")
        if not isinstance(key, str) or not key:
            continue
        target = by_key.get(key)
        if target is None:
            new = dict(p)
            out.append(new)
            by_key[key] = new
            added.append(key)
            continue
        updated.append(key)
        sub_patch = p.get("fields")
        of_patch = p.get("of")
        for k, v in p.items():
            if k in ("fields", "of"):
                continue
            if (k == "lifecycle" and isinstance(v, dict)
                    and isinstance(target.get("lifecycle"), dict)):
                target[k] = merge_lifecycle(target["lifecycle"], v)
                continue
            target[k] = v
        if isinstance(sub_patch, list) and isinstance(target.get("fields"), list):
            target["fields"] = merge_fields(target["fields"], sub_patch)[0]
        elif isinstance(sub_patch, list):
            target["fields"] = [dict(f) for f in sub_patch if isinstance(f, dict)]
        if isinstance(of_patch, dict):
            of_cur = dict(target.get("of") or {})
            of_sub = of_patch.get("fields")
            for k, v in of_patch.items():
                if k != "fields":
                    of_cur[k] = v
            if isinstance(of_sub, list):
                of_cur["fields"] = (merge_fields(of_cur["fields"], of_sub)[0]
                                    if isinstance(of_cur.get("fields"), list)
                                    else [dict(f) for f in of_sub if isinstance(f, dict)])
            target["of"] = of_cur
    return out, added, updated


def remove_fields(current: list, keys: list) -> tuple[list, list[str]]:
    """Retire les fields nommés → `(fields, clés inconnues)`.

    Le retrait est le pendant OBLIGÉ de la fusion : un patch qui ne sait qu'ajouter
    et compléter rend le nettoyage délibéré impossible, et on aurait troqué la
    destruction accidentelle contre l'impossibilité de supprimer. Les deux gestes
    servent à une heure d'intervalle sur un format qui bouge.

    Les clés inconnues sont RENDUES, pas ignorées : un `remove` silencieux sur une
    faute de frappe ferait croire au nettoyage."""
    wanted = {str(k) for k in keys or []}
    kept = [f for f in current
            if not (isinstance(f, dict) and f.get("key") in wanted)]
    present = {f.get("key") for f in current if isinstance(f, dict)}
    return kept, sorted(wanted - present)


def remove_field_attrs(current: list, attrs: dict) -> tuple[list, list[str]]:
    """Retire des ATTRIBUTS nommés sur des colonnes nommées → `(fields, inconnus)`.

    ⚠️ **Le trou que ça ferme, et il était béant.** La fusion COMPLÈTE (`merge_fields`
    : « les propriétés fournies écrasent, les autres sont préservées ») et le retrait
    (`remove_fields`) enlève des COLONNES ENTIÈRES. Entre les deux, rien : pour
    enlever un seul attribut d'une colonne — un `role` mort, une déclaration qu'on
    décommissionne — le seul chemin était de reposer le schéma ENTIER. C'est-à-dire
    le geste qui a détruit 78 notes de champ dans un incident, puis 52 dans un autre,
    en silence (#388).

    *On avait donc troqué la destruction accidentelle contre l'impossibilité de
    retirer un mot* — exactement ce que `remove_fields` dit éviter, un cran plus bas.

    `attrs` = `{colonne: [attribut, …]}`. Descend dans les composites déclarés comme
    la fusion, par le même chemin — sans quoi on ne saurait pas nettoyer un
    sous-champ.

    Les inconnus sont RENDUS, jamais ignorés : sur une faute de frappe, un retrait
    silencieux ferait croire au nettoyage — la moitié du travail d'une garde est de
    refuser de mentir sur ce qu'elle a fait.

    ⚠️ **`key` ne se retire pas** : c'est l'identité de la colonne, pas une de ses
    propriétés. La retirer rendrait le field inadressable, et le schéma le porterait
    sans que personne puisse le désigner pour le réparer."""
    inconnus: list[str] = []
    out: list = []
    demande = {str(k): {str(a) for a in (v or [])} for k, v in (attrs or {}).items()}
    vus: set = set()
    for f in current:
        if not isinstance(f, dict):
            out.append(f)
            continue
        cle = f.get("key")
        retirer = demande.get(str(cle))
        if not retirer:
            out.append(f)
            continue
        vus.add(str(cle))
        if "key" in retirer:
            raise ValueError(
                f"`{cle}` : `key` ne se retire pas — c'est l'identité de la colonne, "
                "pas une de ses propriétés. Pour retirer la colonne entière, c'est "
                "`remove` ; pour la renommer, repose-la sous son nouveau nom.")
        neuf = {k: v for k, v in f.items() if k not in retirer}
        inconnus.extend(f"{cle}.{a}" for a in sorted(retirer) if a not in f)
        out.append(neuf)
    inconnus.extend(sorted(k for k in demande if k not in vus))
    return out, sorted(inconnus)


# ── Ce qu'une POSE de schéma efface (#388) ───────────────────────────────────
#
# `set_schema` pose le schéma ENTIER, sans fusion. Le geste qui ne PEUT pas détruire
# existe (`patch_schema`, fusion par clé), mais `set_schema` reste la bonne façon de
# POSER un format — et rien dans sa réponse ne disait ce qu'il venait d'emporter.
#
# ⚠️ Le point qui fait le signal : **le mode d'écriture était indétectable côté
# appelant**. Sur le même tableau, le même jour, la même session a fait les deux — sa
# migration a PRÉSERVÉ 78 notes de champ (elle patchait le schéma relu en mémoire),
# son remappage en a DÉTRUIT deux (il rebâtissait la liste). Même méthode, même
# succès, réponse identique : il fallait connaître son propre code pour savoir ce
# qu'on venait de perdre, ce qui est hors de portée d'un agent qui exécute une
# procédure écrite par un autre. Deux incidents en deux jours, dont 52 notes.
#
# C'est la forme exacte du défaut corrigé le 27/08 sur les LIGNES (`valeurs_effacees`,
# #407/#408/#409) : une écriture qui efface sans le dire. D'où le même parti, jusqu'au
# nom — on n'empêche rien (retirer un champ est légitime), on NOMME. Et on rend les
# VALEURS : après la pose, la réponse est la seule copie qui reste.

# Ce qui décrit la STRUCTURE plutôt qu'un réglage : `key` identifie l'entrée,
# `fields`/`of` portent les sous-champs — ceux-là sont relevés à leur propre chemin,
# les compter deux fois ferait un relevé qui s'auto-amplifie.
_DECL_STRUCTURELLES = ("key", "fields", "of")
_DECL_NOMMEES = 20
_DECL_VALEUR_MAX = 300


def _field_index(schema: Optional[dict]) -> dict:
    """`{chemin: field-def}` — même convention de chemin que le relevé des lignes en
    place (`occupant.nom`, `contacts[].email`), pour qu'un agent lise le même nom dans
    les deux relevés."""
    out: dict = {}

    def _visiter(fields: list, prefixe: str = "") -> None:
        for f in fields:
            if not isinstance(f, dict) or not f.get("key"):
                continue
            nom = f"{prefixe}{f['key']}"
            out[nom] = f
            if isinstance(f.get("fields"), list):
                _visiter(f["fields"], f"{nom}.")
            of = f.get("of")
            if isinstance(of, dict) and isinstance(of.get("fields"), list):
                _visiter(of["fields"], f"{nom}[].")

    _visiter(_fields(schema))
    return out


def _sous_un_retire(chemin: str, retires: set) -> bool:
    """Le champ vit-il SOUS un champ déjà relevé comme retiré ? Alors sa perte est
    déjà dite par celle de son parent — la répéter gonflerait le compte sans rien
    apprendre."""
    return any(chemin != r and chemin.startswith(r) and chemin[len(r)] in ".["
               for r in retires)


def declarations_effacees(ancien: Optional[dict], nouveau: Optional[dict],
                          annonces: Optional[list] = None) -> list[dict]:
    """Ce que poser `nouveau` retire de `ancien` — `[{champ, retire, declarations}]`.

    `champ = None` désigne la TÊTE du schéma (`key`, `unknown_columns`) : ce qu'on perd en
    premier est `schema.key`, la clé métier, qui porte un index UNIQUE partiel — la
    re-poster absente lève la contrainte sans que rien ne le signale.

    `annonces` = ce dont le retrait est DÉJÀ dit par l'appelant. Deux formes, et la
    seconde est arrivée avec le retrait d'attribut : un **nom de champ** tait le
    retrait du champ entier (le `remove` d'un patch) ; un **`champ.attribut`** tait
    la disparition de cette déclaration-là sur un champ qui, lui, reste
    (`remove_attrs`).

    Les taire n'affaiblit pas le filet : tout ce qui se perd en plus reste relevé —
    c'est même le seul moyen de voir une fusion qui laisserait échapper quelque
    chose. Et un avertissement qui crie sur un geste explicite est celui qu'on
    apprend à ignorer, donc celui qui ruine les vrais.

    Seules les DISPARITIONS comptent, jamais les changements de valeur : réécrire une
    note est un geste qui se nomme lui-même ; la faire disparaître, non."""
    if not isinstance(ancien, dict):
        return []
    tus = {str(a) for a in (annonces or [])}
    sortie: list[dict] = []

    tete_av = {k: v for k, v in ancien.items() if k != "fields"}
    tete_ap = ({k: v for k, v in nouveau.items() if k != "fields"}
               if isinstance(nouveau, dict) else {})
    perdu = {k: v for k, v in tete_av.items() if k not in tete_ap}
    if perdu:
        sortie.append({"champ": None, "retire": False, "declarations": perdu})

    av, ap = _field_index(ancien), _field_index(nouveau)
    retires: set = set()
    for chemin in sorted(av, key=len):            # les parents avant les enfants
        fav = av[chemin]
        if chemin not in ap:
            retires.add(chemin)
            if chemin in tus or _sous_un_retire(chemin, retires):
                continue
            sortie.append({
                "champ": chemin, "retire": True,
                "declarations": {k: v for k, v in fav.items()
                                 if k not in _DECL_STRUCTURELLES}})
            continue
        if chemin in tus:
            continue
        fap = ap[chemin]
        manquantes = {k: v for k, v in fav.items()
                      if k not in _DECL_STRUCTURELLES and k not in fap
                      and f"{chemin}.{k}" not in tus}
        if manquantes:
            sortie.append({"champ": chemin, "retire": False,
                           "declarations": manquantes})
    return sortie


def _decl_rendue(valeur: Any) -> Any:
    """La déclaration perdue, ou sa TAILLE quand la rendre coûterait la réponse.

    La taille, jamais un extrait : projeter n'est pas tronquer — un début de valeur
    ferait croire qu'on l'a lue, alors qu'elle n'est plus nulle part."""
    n = len(valeur) if isinstance(valeur, str) else len(str(valeur))
    if n <= _DECL_VALEUR_MAX:
        return valeur
    return (f"<{n} caractères — la valeur complète n'est plus lisible ici, "
            "elle n'est plus en base non plus>")


def declarations_effacees_report(entrees: list) -> dict:
    """Le relevé prêt à fusionner dans la réponse d'une pose de schéma.

    `{}` quand rien n'a été retiré — le cas normal ne porte pas de clé parasite."""
    if not entrees:
        return {}
    nommees = [{**e, "declarations": {k: _decl_rendue(v)
                                      for k, v in e["declarations"].items()}}
               for e in entrees[:_DECL_NOMMEES]]
    hint = ("cette écriture RETIRE des déclarations que le schéma portait — poser un "
            "schéma le REMPLACE, il ne le fusionne pas, donc tout réglage absent du "
            "corps envoyé disparaît (une note de champ, une borne, des options, une "
            "clé métier et son index UNIQUE). Si ce n'est pas voulu, repose les "
            "valeurs ci-dessus : elles ne sont plus en base. Pour ÉDITER un format "
            "sans risquer d'en perdre une part, `data_patch_schema` fusionne par clé "
            "et ne peut pas détruire ce qu'il ne nomme pas.")
    if len(entrees) > len(nommees):
        hint += (f" {len(entrees)} entrées effacées au total, "
                 f"{len(nommees)} nommées ici.")
    return {"declarations_effacees": nommees,
            "declarations_effacees_hint": hint}
