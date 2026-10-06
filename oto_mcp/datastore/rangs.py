"""L'ÉCRITURE PAR RANG dans une colonne-liste de fiches (oto#22, point c).

Avant ce module, modifier UN attribut d'UN élément obligeait à reposer la liste
entière, couches de tous les éléments réémises : `contacts[0].email` était refusé à
l'écriture alors que la lecture, le filtre et l'agrégat le comprenaient. On écrit
désormais à l'adresse qu'on lit.

## La grammaire — celle de la lecture, un cran de plus

La forme canonique est le CHEMIN À PLAT, celui de `db/paths.split_list_path` : c'est
lui que servent le filtre, le tri et l'agrégat, et c'est la forme qu'une fiche
servie rend pour ses couches (`item["email.comment"]`). Une forme imbriquée
(`{"contacts[0]": {"email": …}}`) aurait fait deux adresses pour une case.

    contacts[0].email            l'attribut `email` de l'élément de rang 0
    contacts[0].email.comment    une couche de cet attribut
    contacts[+]                  un élément AJOUTÉ en fin de liste, fiche complète
                                 — ou une LISTE d'éléments, ajoutés dans l'ordre
    contacts[0]: null            l'élément de rang 0 SUPPRIMÉ
    tags[-]                      une valeur RETIRÉE, ou une liste de valeurs
                                 (liste de valeurs seulement, oto#102)
    journal[+]                   du TEXTE ajouté en fin d'une colonne texte, sur
                                 une nouvelle ligne — ou une liste de lignes

Un attribut s'écrit comme une colonne : valeur nue, `{"valeur": …, "comment": …}`,
`null` (efface l'attribut), `@empty`. Il se FUSIONNE dans l'élément en place par la
règle des colonnes (`_merge_column`) : l'origine survit, `comment`/`link` tombent
avec une valeur qui change, les autres attributs ne bougent pas.

⚠️ **Tous les rangs d'un geste désignent la liste TELLE QU'ELLE EST EN PLACE**, avant
le geste : `{"contacts[0]": null, "contacts[2].email": …}` vise le troisième élément
lu, pas celui qui le deviendrait après la suppression. Puis les retraits par valeur,
puis l'ajout, en dernier.
Le geste se résout SOUS LE VERROU de la ligne, contre la liste exacte — deux rangs
écrits par deux appels concurrents ne s'écrasent pas.

## L'ajout et le retrait sans relecture (oto#102)

Le rang suppose une lecture ; l'ajout et le retrait par valeur n'en demandent aucune,
et c'est ce qui les rend sûrs en concurrence : deux appels qui ajoutent chacun une
entrée à la même ligne en même temps se sérialisent sous le verrou, et la liste finit
avec les DEUX — sans `expected_revision`, sans réservation.

- `col[+]` porte un élément, ou une liste d'éléments ajoutés dans l'ordre. Les
  DOUBLONS sont gardés : une liste est ordonnée, pas un ensemble — un journal peut
  porter deux entrées identiques. Sous `of.key`, une identité doublée reste refusée.
  ⚠️ Une colonne dont les éléments sont eux-mêmes des listes ajoute un élément en
  l'enveloppant : `[[…]]`.
- `col[-]` retire une valeur, ou chaque valeur d'une liste, TOUTES ses occurrences.
  Liste de VALEURS seulement : une fiche n'a pas d'égalité servie (couches, attributs
  absents) et se retire à son rang. Une valeur ABSENTE se refuse, comme un rang hors
  bornes : le plus souvent une faute de frappe, ou un autre geste passé avant — rien
  n'est écrit, et le refus dit ce qui manque.

## L'ajout à une colonne TEXTE

`journal[+]` sur une colonne déclarée `text` — ou, non déclarée, qui porte déjà du
texte — ajoute en fin de cellule, chaque morceau sur sa ligne (`\n` entre l'existant et
l'ajout, et entre deux morceaux d'une liste) ; une cellule vide prend l'ajout tel quel.
Même résolution que l'ajout dans une liste : sous le verrou de la ligne, contre la
valeur en place, donc deux ajouts simultanés arrivent tous les deux, sans réémettre
une cellule de 25 000 caractères. Le texte résultant passe la validation de la colonne
(`max_length`, `pattern`) comme une écriture entière. Seul `[+]` s'y applique : ni
rang, ni retrait — un texte n'a pas d'éléments. Un journal tenu à plusieurs vit mieux
dans une TABLE, une ligne par passage ; la cellule allongée reste un pis-aller.

## Ce qui est refusé, et vers quoi on oriente

- un rang hors bornes : la taille de la liste, le rang demandé, et `contacts[+]` ;
- `contacts[0]: {…}` : la forme imbriquée — on écrit les attributs à leur adresse ;
- `contacts[].email` : TOUS les éléments, une adresse de lecture, pas d'écriture ;
- `contacts[+].email` : un élément s'ajoute ENTIER ;
- `contacts[-]` sur une liste de fiches, ou une fiche à retirer : le rang ;
- `contacts[role=DAF].email` : la désignation par identité (`of.key`) n'est pas
  servie — ni à l'écriture ni à la lecture ; une valeur d'identité qui porterait un
  point ou un crochet casserait la grammaire sans règle de citation ;
- la colonne ENTIÈRE et l'un de ses rangs dans le même geste : deux écritures d'une
  même colonne, l'une écraserait l'autre selon l'ordre.

Ce module ne fusionne rien lui-même : il TRADUIT le geste en une colonne-liste
complète, que les chemins d'écriture font passer par leurs gardes, leur arbitrage
des vides, leur validation et leur journal comme n'importe quelle colonne.
"""
from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from . import schema as dsv2
from .columns import (
    _existing_layers,
    _merge_column,
    _resoudre_la_fiche,
    _scan_mixed,
    mots_dans_l_element,
    refuser_cles_internes,
    reposer_la_liste,
)
from .couches import layer_address
from .declaration import champ_declare, cle_d_element
from .errors import RowValidationError
from .points import _ranger_une_fiche

#: Une adresse de rang : une colonne (sans espace, point ni crochet), un rang entre
#: crochets, et peut-être un attribut. Ce qui ressemble sans être de cette forme —
#: `Prix [EUR]`, `note[a]` — reste un nom de colonne ordinaire, comme avant.
_ADRESSE = re.compile(r"^(?P<col>[^\s\[\].]+)\[(?P<rang>[^\]]*)\](?:\.(?P<reste>.+))?$")
_RANG = re.compile(r"^(?:\d+|[+-]|)$|=")

AJOUT = "+"
RETRAIT = "-"
#: Entre le texte en place et ce que `col[+]` lui ajoute, et entre deux morceaux.
SEPARATEUR_DE_LIGNE = "\n"


def _refus(message: str) -> RowValidationError:
    return RowValidationError([message + " Rien n'a été écrit."])


@dataclass
class _Colonne:
    """Ce qu'un geste écrit par rang dans UNE colonne."""
    nom: str
    modifs: dict = field(default_factory=dict)       # rang → fiche partielle
    suppressions: set = field(default_factory=set)   # rangs supprimés
    ajouts: list = field(default_factory=list)       # éléments ajoutés, dans l'ordre
    retraits: list = field(default_factory=list)     # valeurs retirées (oto#102)


@dataclass
class EcrituresParRang:
    """Le geste par rang d'une écriture, colonne par colonne — sorti du payload par
    `sortir_les_rangs`, préparé (`preparer`), puis résolu contre la ligne en place
    (`appliquer`). `ecrits` = les rangs, dans la liste RÉSULTANTE, des éléments que le
    geste modifie ou ajoute : la validation ne juge qu'eux (J4, oto#140)."""
    colonnes: dict = field(default_factory=dict)
    brut: dict = field(default_factory=dict)
    ecrits: dict = field(default_factory=dict)

    def vise(self, colonne: str) -> bool:
        return colonne in self.colonnes

    # ── la préparation : les gardes du payload, élément par élément ──────────────

    def preparer(self, schema: Optional[dict],
                 normaliser: Callable[[Optional[dict], dict], dict]) -> None:
        """Les gardes que le payload subit sur toute écriture, appliquées à chaque
        élément ÉCRIT, à son VRAI rang : un refus qui nommerait `contacts[0]` pour un
        geste sur `contacts[3]` ferait corriger le mauvais contact.

        Le rangement des couches pointées (`email.comment` à côté d'`email`), les clés
        internes, les mots réservés hors case ou sur l'identité, les couches mal
        orthographiées, puis la normalisation des dates (`normaliser`, celle du store).
        """
        errors: list = []
        for col in self.colonnes.values():
            champ = champ_declare(schema, col.nom)
            cle_item = cle_d_element(champ)
            of = champ.get("of") if isinstance(champ, dict) else None
            for r in sorted(col.modifs):
                col.modifs[r] = self._garder(f"{col.nom}[{r}]", col.modifs[r], schema,
                                             of, cle_item, errors, normaliser)
            for liste, signe in ((col.ajouts, AJOUT), (col.retraits, RETRAIT)):
                for i, element in enumerate(liste):
                    adresse = (f"{col.nom}[{signe}]" if len(liste) == 1
                               else f"{col.nom}[{signe}][{i}]")
                    liste[i] = self._garder(adresse, element, schema, of, cle_item,
                                            errors, normaliser)
        if errors:
            raise RowValidationError(errors)

    @staticmethod
    def _garder(adresse: str, element: Any, schema: Optional[dict], of: Any,
                cle_item: Optional[str], errors: list,
                normaliser: Callable[[Optional[dict], dict], dict]) -> Any:
        if isinstance(element, dict):
            element = _ranger_une_fiche(element, adresse)
        refuser_cles_internes({adresse: element})
        mots_dans_l_element(element, adresse, errors, cle_item=cle_item)
        _scan_mixed(element, adresse, errors)
        return _dates(schema, of, adresse, element, normaliser)

    # ── la résolution : contre la ligne EN PLACE ──────────────────────────────────

    def appliquer(self, en_place: Optional[dict], schema: Optional[dict], *,
                  creation: bool = False) -> dict:
        """`{colonne: liste résultante}` — `None` quand le geste vide la liste (la
        colonne s'efface alors comme sous un `null`, et la valeur partie se relève).

        `en_place` = la ligne lue sous le verrou ; `{}` sur une création, où il n'y a
        aucun élément à viser."""
        out: dict = {}
        self.ecrits = {}
        for col in self.colonnes.values():
            cellule = (en_place or {}).get(col.nom)
            if _ajout_de_texte(champ_declare(schema, col.nom), cellule, col):
                out[col.nom] = _ajouter_le_texte(col, cellule)
                continue
            avant = _liste_en_place(col.nom, cellule, creation)
            cle_item = cle_d_element(champ_declare(schema, col.nom))
            vises = sorted(set(col.modifs) | col.suppressions)
            if vises and vises[-1] >= len(avant):
                raise _refus_hors_bornes(col.nom, vises[-1], len(avant), creation)
            # `(élément, écrit par le geste ?)` : les rangs écrits se relisent à la
            # fin, après les suppressions, les retraits et les ajouts qui les décalent.
            suivis = [(_fusionner_l_element(col.nom, r, x, col.modifs[r]), True)
                      if r in col.modifs else (x, False) for r, x in enumerate(avant)]
            for r in sorted(col.suppressions, reverse=True):
                del suivis[r]
            if col.retraits:
                suivis = _retirer(col.nom, suivis, col.retraits, creation)
            suivis += [(_nouvel_element(x), True) for x in col.ajouts]
            apres = [x for x, _ in suivis]
            if cle_item:
                _refuser_identite_doublee(col.nom, cle_item, avant, apres)
            self.ecrits[col.nom] = {i for i, (_, ecrit) in enumerate(suivis) if ecrit}
            out[col.nom] = apres or None
        return out


def sortir_les_rangs(schema: Optional[dict], user_data: Optional[dict]
                     ) -> tuple[dict, Optional[EcrituresParRang]]:
    """`(payload sans les adresses de rang, geste par rang ou None)`.

    Lit la grammaire et refuse ce qu'elle ne sert pas — AVANT toute autre garde : une
    adresse de rang laissée dans le payload serait jugée comme un nom de colonne
    pointé, et `_refuse_dotted_names` la refuserait pour une raison fausse."""
    if not user_data:
        return dict(user_data or {}), None
    reste: dict = {}
    rangs = EcrituresParRang()
    couches: dict = {}
    for cle, valeur in user_data.items():
        m = _ADRESSE.match(cle) if isinstance(cle, str) else None
        if m is None or not _RANG.search(m.group("rang")):
            reste[cle] = valeur
            continue
        nom, rang, attribut = m.group("col"), m.group("rang"), m.group("reste")
        _refuser_la_forme(cle, nom, rang, attribut, valeur, schema)
        rangs.brut[cle] = valeur
        col = rangs.colonnes.setdefault(nom, _Colonne(nom))
        if rang in (AJOUT, RETRAIT):
            elements = list(valeur) if isinstance(valeur, list) else [valeur]
            (col.ajouts if rang == AJOUT else col.retraits).extend(elements)
            continue
        r = int(rang)
        if attribut is None:
            col.suppressions.add(r)
            continue
        adresse = layer_address(attribut)
        if adresse is not None:
            couches.setdefault((nom, r), []).append((cle, *adresse, valeur))
        else:
            col.modifs.setdefault(r, {})[attribut] = valeur
    if not rangs.colonnes:
        return reste, None
    # Une couche nommée SEULE annote l'attribut en place : on la range imbriquée. À
    # côté de son attribut, elle reste pointée — `_ranger_une_fiche` les réunit, avec
    # le refus de collision des colonnes.
    for (nom, r), liste in couches.items():
        fiche = rangs.colonnes[nom].modifs.setdefault(r, {})
        for _cle, attribut, couche, valeur in liste:
            if attribut in fiche:
                fiche[f"{attribut}.{couche}"] = valeur
            else:
                fiche[attribut] = {couche: valeur}
    for nom, col in rangs.colonnes.items():
        if nom in reste:
            raise _refus(
                f"`{nom}` est écrite ENTIÈRE et par rang ({_cites(rangs.brut, nom)}) dans "
                f"le même geste : deux écritures d'une même colonne, l'une écraserait "
                f"l'autre. Garde l'une des deux formes.")
        doubles = sorted(set(col.modifs) & col.suppressions)
        if doubles:
            r = doubles[0]
            raise _refus(
                f"`{nom}[{r}]` est supprimé et modifié dans le même geste. Garde l'un "
                f"des deux : `{nom}[{r}]: null` supprime l'élément, "
                f"`{nom}[{r}].<attribut>` le modifie.")
    return reste, rangs


def fusionner(rangs: Optional[EcrituresParRang], cle: str, existant: Any,
              valeur: Any, champ: Any) -> Any:
    """La fusion d'une colonne dans la ligne. Une colonne écrite par rang arrive DÉJÀ
    fusionnée élément par élément (`appliquer`) : on la repose dans ses couches de
    colonne sans la refusionner — `_merge_column` la remplacerait en bloc, ou, sous
    `of.key`, reprendrait un attribut que le geste vient d'effacer."""
    if rangs is not None and rangs.vise(cle) and isinstance(valeur, list):
        return reposer_la_liste(existant, valeur)
    return _merge_column(existant, valeur, champ)


# ── les refus de la grammaire ───────────────────────────────────────────────────

def _refuser_la_forme(cle: str, nom: str, rang: str, attribut: Optional[str],
                      valeur: Any, schema: Optional[dict]) -> None:
    champ = champ_declare(schema, nom)
    if champ is not None and champ.get("type") != "list":
        if champ.get("type") == "text" and rang == AJOUT and attribut is None:
            _refuser_le_texte(cle, nom, valeur)
            return
        if rang == AJOUT:
            raise _refus(
                f"`{cle}` ajoute à `{nom}`, déclarée `{champ.get('type')}` : `[+]` "
                f"n'ajoute qu'à une colonne-liste (un élément) ou texte (une ligne). "
                f"Écris `{nom}` entière.")
        raise _refus(
            f"`{cle}` vise un rang de `{nom}`, déclarée `{champ.get('type')}` : seule une "
            f"colonne-liste (`type: list`) s'adresse par rang. Écris `{nom}` entière.")
    if rang == "":
        raise _refus(
            f"`{cle}` désigne TOUS les éléments de `{nom}` : c'est une adresse de "
            f"lecture (filtre, agrégat), pas d'écriture. Vise un rang — "
            f"`{nom}[0]{'.' + attribut if attribut else ''}` — lu dans data_rows.")
    if "=" in rang:
        raise _refus(
            f"`{cle}` désigne un élément par son identité : ce n'est pas servi. Vise "
            f"son RANG, lu dans data_rows — `{nom}[0]"
            f"{'.' + attribut if attribut else ''}`. Les rangs d'un geste désignent la "
            f"liste telle qu'elle est en place.")
    if rang == AJOUT:
        if attribut is not None:
            raise _refus(
                f"`{cle}` : un élément s'ajoute ENTIER, en une fiche — "
                f'`"{nom}[+]": {{"{attribut.split(".")[0]}": …, …}}`. Pour modifier un '
                f"élément existant : `{nom}[<rang>].{attribut}`.")
        _refuser_le_vide(cle, nom, valeur,
                         "ajoute un élément — une fiche (`{…}`) ou, dans une liste de "
                         "valeurs, une valeur — ou une LISTE d'éléments, ajoutés dans "
                         "l'ordre")
        return
    if rang == RETRAIT:
        _refuser_le_retrait(cle, nom, attribut, valeur, champ)
        return
    if attribut is None:
        if valeur is not None:
            raise _refus(
                f"`{cle}` : un élément se modifie attribut par attribut — "
                f"`{nom}[{int(rang)}].<attribut>` —, se supprime par "
                f"`{nom}[{int(rang)}]: null`, s'ajoute par `{nom}[+]`. La forme "
                f"imbriquée n'est pas servie : une case n'a qu'une adresse.")
        return
    if "." in attribut and layer_address(attribut) is None:
        tete = attribut.split(".")[0]
        raise _refus(
            f"`{cle}` descend sous l'attribut `{tete}` : un attribut d'élément s'écrit "
            f"entier — `{nom}[{int(rang)}].{tete}` —, ou par l'une de ses couches "
            f"({', '.join('`' + c + '`' for c in dsv2.LAYER_KEYS)}).")


def _refuser_le_texte(cle: str, nom: str, valeur: Any) -> None:
    """`journal[+]` sur une colonne texte : une chaîne, ou une liste de chaînes."""
    _refuser_le_vide(cle, nom, valeur,
                     "ajoute du texte en fin de cellule — une chaîne, ou une liste de "
                     "chaînes, chacune sur sa ligne")
    morceaux = valeur if isinstance(valeur, list) else [valeur]
    if not all(isinstance(x, str) for x in morceaux):
        raise _refus(
            f"`{cle}` ajoute du TEXTE à `{nom}` : une chaîne, ou une liste de chaînes ; "
            f"reçu {_forme(next(x for x in morceaux if not isinstance(x, str)))}. Pour "
            f"poser une autre forme, écris `{nom}` entière.")
    if any(x == "" for x in morceaux):
        raise _refus(
            f"`{cle}` : une chaîne vide n'ajoute rien à `{nom}`. Pour l'effacer : "
            f"`\"{nom}\": null`.")


def _ajout_de_texte(champ: Optional[dict], cellule: Any, col: _Colonne) -> bool:
    """Le geste est-il un ajout à une colonne TEXTE ? Déclarée `text` ; non déclarée,
    seulement si elle porte déjà du texte — sur une case vide, `[+]` reste l'ajout
    d'élément d'une liste, comme avant. Un rang ou un retrait n'en est jamais un : un
    texte n'a pas d'éléments, et `_liste_en_place` le refuse en le disant."""
    if col.modifs or col.suppressions or col.retraits:
        return False
    if champ is not None:
        return champ.get("type") == "text"
    return isinstance(_existing_layers(cellule).get(dsv2.VALUE_LAYER), str)


def _ajouter_le_texte(col: _Colonne, cellule: Any) -> str:
    """Le texte en place, puis chaque morceau ajouté, sur sa ligne."""
    _refuser_le_texte(f"{col.nom}[{AJOUT}]", col.nom,
                      col.ajouts if len(col.ajouts) != 1 else col.ajouts[0])
    avant = _existing_layers(cellule).get(dsv2.VALUE_LAYER)
    if avant is not None and not isinstance(avant, str):
        raise _refus(
            f"`{col.nom}` porte une valeur qui n'est pas du texte ({_forme(avant)}) : "
            f"`{col.nom}[+]` n'y ajoute pas de ligne. Écris `{col.nom}` entière.")
    return SEPARATEUR_DE_LIGNE.join(([avant] if avant else []) + list(col.ajouts))


def _refuser_le_retrait(cle: str, nom: str, attribut: Optional[str], valeur: Any,
                        champ: Optional[dict]) -> None:
    """`tags[-]` retire des VALEURS : sur une liste de fiches, le rang reste le geste."""
    of = champ.get("of") if isinstance(champ, dict) else None
    fiches = isinstance(of, dict) and (of.get("type") == "object"
                                       or isinstance(of.get("fields"), list))
    valeurs = valeur if isinstance(valeur, list) else [valeur]
    if attribut is not None or fiches or any(isinstance(x, dict) for x in valeurs):
        raise _refus(
            f"`{cle}` : le retrait par valeur ne vise qu'une liste de VALEURS — une "
            f"fiche n'a pas d'égalité servie (couches, attributs absents). Retire-la à "
            f"son rang, lu dans data_rows : `\"{nom}[<rang>]\": null`.")
    _refuser_le_vide(cle, nom, valeur, "retire une valeur, ou chaque valeur d'une liste")


def _refuser_le_vide(cle: str, nom: str, valeur: Any, geste: str) -> None:
    """`null`, `[]` ou un `null` dans la liste : rien à ajouter ni à retirer."""
    if valeur is None:
        recu = "`null`"
    elif valeur == []:
        recu = "une liste vide"
    elif isinstance(valeur, list) and any(x is None for x in valeur):
        recu = "un `null` dans la liste"
    else:
        return
    raise _refus(
        f"`{cle}` {geste} ; reçu {recu}, qui ne désigne aucun élément. Pour effacer la "
        f"colonne : `\"{nom}\": null`.")


def _meme_valeur(a: Any, b: Any) -> bool:
    """L'égalité JSON : `true` n'est pas `1`, ni `"1"` ; `1` et `1.0` le sont."""
    if isinstance(a, bool) or isinstance(b, bool):
        return type(a) is type(b) and a == b
    return a == b


def _retirer(nom: str, suivis: list, retraits: list, creation: bool) -> list:
    """Chaque valeur de `retraits` sort de la liste, TOUTES ses occurrences. Une valeur
    absente se refuse : rien n'est écrit, et le refus la nomme."""
    absentes = [v for v in retraits if not any(_meme_valeur(x, v) for x, _ in suivis)]
    if absentes:
        if creation:
            etat = f"cette écriture CRÉE la ligne : `{nom}` n'y a encore aucun élément"
        else:
            etat = (f"`{nom}` ne porte pas "
                    + ", ".join(f"`{json.dumps(v, ensure_ascii=False)}`" for v in absentes)
                    + f" ({len(suivis)} élément{'s' if len(suivis) > 1 else ''} en place)")
        raise _refus(
            f"{etat} — rien à retirer. Relis la ligne : une valeur mal orthographiée, ou "
            f"déjà retirée par un autre geste.")
    return [(x, e) for x, e in suivis
            if not any(_meme_valeur(x, v) for v in retraits)]


def _refus_hors_bornes(nom: str, rang: int, taille: int, creation: bool
                       ) -> RowValidationError:
    if creation:
        etat = f"cette écriture CRÉE la ligne : `{nom}` n'y a encore aucun élément"
    elif taille == 0:
        etat = f"`{nom}` n'a aucun élément sur cette ligne"
    else:
        etat = (f"`{nom}` a {taille} élément{'s' if taille > 1 else ''} "
                f"(rangs 0 à {taille - 1})")
    return _refus(f"{etat} ; rang {rang} inexistant. Pour ajouter : `{nom}[+]`.")


def _liste_en_place(nom: str, cellule: Any, creation: bool) -> list:
    valeur = _existing_layers(cellule).get(dsv2.VALUE_LAYER)
    if valeur is None:
        return []
    if not isinstance(valeur, list):
        raise _refus(
            f"`{nom}` porte une valeur qui n'est pas une liste ({_forme(valeur)}) : un "
            f"rang ne s'y adresse pas. Écris `{nom}` entière — et si `{nom}[…]` est le "
            f"NOM d'une colonne, renomme-la : un crochet désigne un rang.")
    return valeur


def _fusionner_l_element(nom: str, rang: int, element: Any, partielle: dict) -> dict:
    """L'élément en place, ses attributs nommés fusionnés par la règle des colonnes —
    exactement ce que `_merge_items` fait d'un élément apparié par `of.key`."""
    if not isinstance(element, dict):
        raise _refus(
            f"`{nom}[{rang}]` n'est pas une fiche ({_forme(element)}) : il n'a pas "
            f"d'attribut. Supprime-le (`{nom}[{rang}]: null`) puis ajoute la fiche "
            f"(`{nom}[+]`), ou écris `{nom}` entière.")
    fusion = dict(element)
    for attribut, cellule in partielle.items():
        v = _merge_column(element.get(attribut), cellule)
        if v is None:
            fusion.pop(attribut, None)
        else:
            fusion[attribut] = v
    return fusion


def _nouvel_element(element: Any) -> Any:
    """L'élément AJOUTÉ n'a rien en place : son `@empty` se résout contre rien (même
    règle qu'un élément neuf de `_merge_items`)."""
    if not isinstance(element, dict):
        return element
    return _resoudre_la_fiche(element)


def _identites(liste: list, cle: str) -> list:
    return [dsv2.unwrap(x.get(cle)) for x in liste if isinstance(x, dict)
            and dsv2.unwrap(x.get(cle)) not in (None, "")]


def _refuser_identite_doublee(nom: str, cle: str, avant: list, apres: list) -> None:
    """Sous `of.key`, deux éléments ne portent pas la même identité : le geste par
    rang ne doit pas fabriquer le doublon que la liste posée entière refuse."""
    deja = {v for v, n in Counter(_identites(avant, cle)).items() if n > 1}
    doubles = {v for v, n in Counter(_identites(apres, cle)).items()
               if n > 1 and v not in deja}
    if doubles:
        raise _refus(
            f"`{cle}` en double dans `{nom}` après ce geste : "
            + ", ".join(repr(d) for d in sorted(doubles, key=str))
            + f" — `{cle}` est l'identité des éléments (`of.key`), deux éléments ne la "
            f"partagent pas. Modifie l'élément qui la porte déjà, à son rang.")


def _dates(schema: Optional[dict], of: Any, adresse: str, element: Any,
           normaliser: Callable[[Optional[dict], dict], dict]) -> Any:
    """Les dates d'un élément normalisées par la règle du store, l'élément déclaré à
    son adresse — les notices nomment `contacts[3].debut`, pas un rang reconstitué."""
    if not isinstance(of, dict):
        return element
    if isinstance(element, dict):
        decl = {"key": adresse, "type": "object", "fields": of.get("fields") or []}
    else:
        decl = {**of, "key": adresse}
    mini = {**(schema or {}), "fields": [decl]}
    return normaliser(mini, {adresse: element}).get(adresse, element)


def _forme(valeur: Any) -> str:
    if isinstance(valeur, dict):
        return "un objet"
    if isinstance(valeur, list):
        return "une liste"
    return repr(valeur)


def _cites(brut: dict, nom: str) -> str:
    return ", ".join(f"`{c}`" for c in brut if c.split("[")[0] == nom)
