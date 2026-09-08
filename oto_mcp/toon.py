"""Encodage TOON du canal TEXTE d'un résultat d'outil — tabulaire, et rien d'autre.

TOON (Token-Oriented Object Notation, spec v4.1) déclare les colonnes UNE fois puis
écrit une ligne par enregistrement. Sur une liste d'objets plats et uniformes, ça
retire le nom de chaque clé répété à chaque ligne — le poste dominant d'un JSON de
liste. Ce module n'écrit QUE cette forme, et **refuse tout le reste** en rendant
`None` : l'appelant garde alors son JSON.

⚠️ **Le format ne se choisit pas par outil, il se choisit par CHARGE.** Mesuré le
08/09/2026 sur des charges réelles de notre propre org : un relevé de monitoring
(champs courts, lignes uniformes) gagne 33 %, tandis qu'une table à prose gagne
12,5 % *au mieux* et **perd 7,6 %** telle quelle, parce qu'une seule ligne à qui il
manque une colonne fait basculer tout le bloc en forme de liste — plus verbeuse que
le JSON qu'elle remplace. Deux tables du même outil tombent donc de part et d'autre
du seuil. D'où `choisir()` : on encode, on compare, on garde le plus court. Une
liste blanche d'outils aurait embarqué la régression avec le gain.

Pas de décodeur : oto écrit du TOON, il n'en relit jamais. Ce qui revient d'un client
reste du JSON.

Règles de citation, dérivées par différentiel contre l'encodeur de référence
(`@toon-format/toon` 4.1.1) le 08/09/2026, pas d'après la prose de la spec :
citation si la valeur contient `, : " \\ [ ] { }` ou un caractère de contrôle, si
elle commence par `-` ou `#`, si elle a une espace en tête ou en queue, si elle est
vide, si elle ressemble à un nombre, ou si elle vaut exactement `true`/`false`/`null`.
Le reste passe nu — `|`, `>`, `&`, `*`, `@`, `?`, `/`, `'` compris, en tête comme au
milieu, et `-`/`#` au milieu.

⚠️ Un **NOM** (clé de premier niveau, colonne d'en-tête) suit la règle INVERSE : une
liste blanche `[A-Za-z0-9_.]`, plus le refus des noms numériques. `a-b` se cite donc en
NOM et passe nu en VALEUR ; `true` fait l'exact contraire. Appliquer la règle des
valeurs aux deux produit un en-tête que la référence n'écrirait pas — c'est le premier
défaut qu'a trouvé le différentiel, et aucune relecture ne l'aurait vu.
"""
from __future__ import annotations

import json
import re

# Un nombre au sens de l'encodeur de référence : `00`, `+1` et `1e5` en sont, `.5`,
# `1.` et `0x1` n'en sont pas. Une chaîne qui matche se cite, sinon elle se lirait
# comme un nombre à la relecture.
_NOMBRE = re.compile(r"^[+-]?\d+(\.\d+)?([eE][+-]?\d+)?$")

# Contenus qui imposent la citation où qu'ils se trouvent dans la valeur.
_TOUJOURS = set(',:"\\[]{}')

# Contenus qui ne l'imposent qu'en PREMIER caractère (`a-b` et `a#b` passent nus).
_EN_TETE = ("-", "#")

# Littéraux du langage : une chaîne qui les vaut exactement se cite, sinon elle
# se relirait comme le scalaire. La casse compte — `True` et `Null` passent nus.
_LITTERAUX = {"true", "false", "null"}

# ⚠️ Un NOM (clé de premier niveau, colonne d'en-tête) suit la règle INVERSE d'une
# valeur : une liste blanche, pas une liste noire. Dérivé par différentiel sur 38 noms
# le 08/09/2026 — `a-b` se cite en NOM alors qu'il passe nu en VALEUR, et `true` passe
# nu en NOM alors qu'il se cite en VALEUR. Prendre la règle des valeurs pour les deux
# produit un en-tête que la référence n'écrirait pas.
_NOM_NU = re.compile(r"^[A-Za-z0-9_.]+$")

# Garde-fou de boucle (le serveur est mono-loop, cf. docs/conventions.md) : au-delà,
# on ne tente même pas l'encodage, on rend le JSON. Une page d'outil qui dépasse ça
# a un problème de pagination, que le format ne réglera pas.
LIGNES_MAX = 5_000


def _cite(valeur: str) -> str:
    """Rend `valeur` citée et échappée, à la façon de l'encodeur de référence."""
    court = {"\\": "\\\\", '"': '\\"', "\n": "\\n", "\r": "\\r", "\t": "\\t"}
    morceaux = []
    for c in valeur:
        if c in court:
            morceaux.append(court[c])
        elif ord(c) < 0x20:
            # Les autres caractères de contrôle partent en `\uXXXX` — bruts entre
            # guillemets, ils casseraient la ligne à la relecture. DEL (0x7f) n'en
            # est pas un pour la référence et passe tel quel.
            morceaux.append("\\u%04x" % ord(c))
        else:
            morceaux.append(c)
    return '"' + "".join(morceaux) + '"'


def _nom(cle: object) -> str | None:
    """Rend une CLÉ (premier niveau ou colonne d'en-tête), ou `None` si ce n'est pas
    une chaîne. Liste blanche — cf. `_NOM_NU`, et ce n'est pas la règle des valeurs."""
    if not isinstance(cle, str):
        return None
    if _NOM_NU.match(cle) and not _NOMBRE.match(cle):
        return cle
    return _cite(cle)


def _scalaire(valeur: object) -> str | None:
    """Rend un scalaire JSON en TOON, ou `None` si ce n'en est pas un.

    ⚠️ `bool` se teste AVANT `int` : en Python `True` est un `int`, et le tester
    après rendrait `1` là où la donnée dit `true`.
    """
    if valeur is None:
        return "null"
    if isinstance(valeur, bool):
        return "true" if valeur else "false"
    if isinstance(valeur, (int, float)):
        # `json.dumps` d'un float rend la même forme que l'encodeur de référence
        # (`1.5`, `1e+30`) ; `repr` diverge sur les grands exposants.
        #
        # ⚠️ DEUX divergences ASSUMÉES avec l'implémentation de référence, toutes deux
        # dues au modèle de nombre de JavaScript, et où c'est elle qui perd :
        # un entier au-delà de 2^53 y est ARRONDI (9007199254740993 devient …992), et
        # un float entier y perd sa décimale (`2.0` devient `2`). Python tient les
        # deux. On ne recopie pas une perte de donnée pour ressembler à la référence —
        # `tests/test_toon.py` fige ces deux cas pour que le choix reste délibéré.
        return json.dumps(valeur)
    if isinstance(valeur, str):
        if (
            valeur == ""
            or valeur != valeur.strip()
            or any(c in _TOUJOURS for c in valeur)
            or any(ord(c) < 0x20 for c in valeur)
            or valeur.startswith(_EN_TETE)
            or valeur in _LITTERAUX
            or _NOMBRE.match(valeur)
        ):
            return _cite(valeur)
        return valeur
    return None


def _plat(objet: object) -> bool:
    """Vrai si `objet` est un dict dont TOUTES les valeurs sont des scalaires.

    Une valeur imbriquée (dict ou liste) est refusée ici alors que la spec sait la
    replier : ce module reste sur la forme qui paie, et un repli mal rendu coûterait
    plus cher à déboguer que les quelques pour-cent qu'il rapporterait.
    """
    return isinstance(objet, dict) and all(
        v is None or isinstance(v, (bool, int, float, str)) for v in objet.values()
    )


def _tableau(cle: str, lignes: list) -> list[str] | None:
    """Bloc tabulaire pour `lignes`, ou `None` si la forme ne s'y prête pas.

    Exige des dicts PLATS, tous porteurs des MÊMES clés, dans le même ordre que la
    première ligne. ⚠️ On n'uniformise PAS une liste qui ne l'est pas : compléter les
    trous à `null` ferait voir à l'agent des colonnes que la donnée ne porte pas, et
    la mesure du 08/09 dit que ça ne rachète pas le format de toute façon.
    """
    if not lignes or len(lignes) > LIGNES_MAX:
        return None
    if not all(_plat(ligne) for ligne in lignes):
        return None
    colonnes = list(lignes[0].keys())
    if not colonnes:
        return None
    reference = set(colonnes)
    if any(set(ligne.keys()) != reference for ligne in lignes):
        return None
    # Une colonne nommée `a,b` casserait l'en-tête en deux : les noms ont leur
    # propre règle de citation (`_nom`), plus stricte que celle des valeurs.
    entetes = [_nom(c) for c in colonnes]
    if any(e is None for e in entetes):
        return None
    out = [f"{cle}[{len(lignes)}]{{{','.join(entetes)}}}:"]
    for ligne in lignes:
        cellules = [_scalaire(ligne[c]) for c in colonnes]
        if any(c is None for c in cellules):
            return None
        out.append("  " + ",".join(cellules))
    return out


def _inline(cle: str, valeurs: list) -> list[str] | None:
    """Forme en ligne d'un tableau de scalaires : `cle[N]: a,b,c`."""
    rendus = [_scalaire(v) for v in valeurs]
    if any(r is None for r in rendus):
        return None
    return [f"{cle}[{len(valeurs)}]: {','.join(rendus)}"]


def encode(charge: object) -> str | None:
    """Rend `charge` en TOON, ou `None` si sa forme n'est pas de celles qui paient.

    Accepte un dict de premier niveau dont chaque valeur est : un scalaire, une liste
    vide, une liste de scalaires, ou une liste d'objets plats et uniformes. Refuse
    tout le reste — dict imbriqué, liste non uniforme, liste de listes — parce qu'un
    refus rend la main au JSON, qui est correct par construction.
    """
    if not isinstance(charge, dict) or not charge:
        return None
    lignes: list[str] = []
    a_un_tableau = False
    for cle, valeur in charge.items():
        nom = _nom(cle)
        if nom is None:
            return None
        if isinstance(valeur, list):
            if not valeur:
                lignes.append(f"{nom}: []")
                continue
            if all(isinstance(v, dict) for v in valeur):
                bloc = _tableau(nom, valeur)
                if bloc is None:
                    return None
                a_un_tableau = True
                lignes.extend(bloc)
                continue
            bloc = _inline(nom, valeur)
            if bloc is None:
                return None
            lignes.extend(bloc)
            continue
        rendu = _scalaire(valeur)
        if rendu is None:
            return None
        lignes.append(f"{nom}: {rendu}")
    # Sans bloc tabulaire il n'y a rien à gagner : la forme `cle: valeur` pèse ce que
    # pèse le JSON, à la ponctuation près, et diverger de format pour ça ne se paie pas.
    if not a_un_tableau:
        return None
    return "\n".join(lignes)


def choisir(charge: object, texte_json: str, marge: float = 0.15) -> str | None:
    """Rend le TOON s'il est plus court que `texte_json` d'au moins `marge`, sinon `None`.

    C'est la décision PAR CHARGE annoncée en tête de module, et le seul garde-fou qui
    tienne : la même liste d'outils sert des tables de compteurs et des tables de
    prose, et seule la charge dit de quel côté du seuil elle tombe. La marge par
    défaut vaut 15 % en CARACTÈRES, mesurés sur le texte réellement servi — un proxy
    du compte de tokens, choisi parce que tokeniser dans la boucle coûterait plus que
    ce qu'on économise.
    """
    if not texte_json:
        return None
    rendu = encode(charge)
    if rendu is None:
        return None
    if len(rendu) > len(texte_json) * (1.0 - marge):
        return None
    return rendu
