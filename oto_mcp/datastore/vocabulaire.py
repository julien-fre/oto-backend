"""Ce que CETTE version lit et fait respecter — dérivé du code, jamais recopié.

Un client ne peut pas opposer une documentation au serveur qui lui répond. Ce module
lui rend deux relevés, et **aucun des deux n'est une liste** :

- `enforced_keys` — les clés de validation que ce déploiement EXÉCUTE, établies en
  faisant tourner le validateur sur des sondes (`_ENFORCEMENT_PROBES`) : un schéma
  minimal qui doit être refusé, et parfois un témoin qui doit passer. Une clé est
  annoncée si, et seulement si, elle mord ici et maintenant ;
- `interpreted_keys` — les clés que le code LIT, dérivées de son propre source par AST
  (`_read_keys`). ⚠️ Depuis le 01/10/2026 ce relevé ne FONDE plus rien : il en
  comptait trop (`strict` ou `states` posés sur une colonne passaient, lus à un autre
  niveau). Le refus des clés inconnues repose sur la DÉCLARATION par niveau
  (`schema_keys.ADMISES`) ; ce relevé n'en est plus que la garde de banc — tout ce que
  le code lit doit être déclaré (`tests/test_schema_keys_oto56.py`).

⚠️ **`_read_keys` scanne une liste de FICHIERS.** Un module du paquet qui se met à
lire un attribut de colonne doit y être ajouté, sinon la garde ne le voit pas et la
clé pourrait n'être déclarée nulle part — donc refusée. C'est la seule dépendance de ce
fichier envers la DISPOSITION du code, et elle est explicite pour cette raison.

Ce qu'il ne tient pas :
- **la liste DÉCLARÉE des attributs admis, par niveau, et le refus du reste** →
  `schema_keys.py` et `cles_inconnues.py` ;
- **ce qu'un tableau déclare et que le moteur laisse inerte** → `non_applique.py` ;
- **les clés hors référentiel d'une LIGNE** → `hors_schema.py` : ici, un format.
"""
from __future__ import annotations

from typing import Optional

from . import claimable

from .declaration import key_required_of
from .hors_schema import off_schema_refusal
from .champs_reserves import reserved_refusals
from .validation import validate_row

# ── Ce que CETTE version fait respecter (#389) ───────────────────────────────
#
# Le signal qui rendait les autres dangereux : il ne demandait pas une contrainte de
# plus, il demandait de savoir lesquelles MORDENT. Deux cas vécus le même jour, et le
# second est le vrai sujet — l'écart n'était pas dans le vocabulaire mais dans le
# DÉPLOIEMENT. `max_length: 60` posé sur quatre colonnes d'un tableau de production,
# code de validation écrit le jour même, version déployée qui ne l'exécutait pas
# encore : un PATCH idempotent rendait 200, et avec le code à jour 75 lignes sur 600
# devenaient inécritables. Effet DIFFÉRÉ au prochain déploiement, MASSIF, SIMULTANÉ,
# et de cause vieille de plusieurs semaines — personne ne relie « les agents
# n'écrivent plus sur ces lignes » à « quelqu'un a posé une borne un mardi ».
#
# Le refus des clés inconnues (`cles_inconnues`) dit la moitié NÉGATIVE — « cette
# clé, je ne la connais pas ». Il manquait la moitié POSITIVE, la seule qu'un client
# puisse vérifier contre le serveur qui lui répond plutôt que contre une documentation.
#
# ⚠️ **Le relevé s'établit en FAISANT TOURNER le validateur**, jamais en recopiant une
# liste. Une liste parallèle diverge le jour où quelqu'un exécute une clé de plus (ou
# cesse d'en exécuter une), et elle se met alors à mentir dans les deux sens — ce que
# le signal reproche au silence. Chaque sonde est un schéma minimal + une ligne qui le
# viole : la clé est annoncée si, et seulement si, `validate_row` refuse ici et
# maintenant. C'est le même parti que `interpreted_keys` (dérivé du code), poussé d'un
# cran : dérivé du COMPORTEMENT, donc insensible à la façon dont le code est écrit.

# `(clé, schéma qui doit REFUSER, ligne fautive, témoin qui doit PASSER ou None)`.
# Le témoin ne sert qu'aux clés dont l'effet est d'ARMER autre chose : `strict`
# n'interdit rien par lui-même, il rend la conformité de type opposable. Sans le
# témoin, on l'annoncerait dès que le type est vérifié, ce qui serait vrai par
# accident.
_ENFORCEMENT_PROBES = (
    ("required",
     {"fields": [{"key": "x", "required": True}]}, {}, None),
    ("required_when",
     {"fields": [{"key": "x", "required_when": {"y": "1"}}, {"key": "y"}]},
     {"y": "1"}, None),
    ("max_length",
     {"fields": [{"key": "x", "max_length": 1}]}, {"x": "ab"}, None),
    ("pattern",
     {"fields": [{"key": "x", "max_length": 8, "pattern": "^ok$"}]},
     {"x": "non"}, None),
    ("max_items",
     {"strict": True,
      "fields": [{"key": "x", "type": "list", "of": {"type": "text"},
                  "max_items": 1}]},
     {"x": ["a", "b"]}, None),
    # ⚠️ Sonde passée d'`enum` à `text` le 10/09/2026 (#98). Sur un enum, elle
    # annonçait `options` appliquée pendant qu'une liste posée sur un texte, un json ou
    # une colonne sans type ne refusait RIEN, tableau strict compris : le client qui
    # lisait `enforced` se croyait protégé. Elle éprouve désormais le cas général —
    # celui qui était cassé —, et l'annonce retombera si le trou se rouvre.
    ("options",
     {"strict": True,
      "fields": [{"key": "x", "type": "text", "options": ["a"]}]},
     {"x": "b"}, None),
    ("type",
     {"strict": True, "fields": [{"key": "x", "type": "number"}]},
     {"x": "abc"}, None),
    # ⚠️ Sonde CHANGÉE le 08/09/2026, et le motif importe. Elle opposait un schéma
    # strict à un schéma libre sur une valeur de mauvais TYPE — ce qui supposait que le
    # type ne soit pas vérifié sans `strict`. Depuis que le type déclaré s'arme
    # lui-même, les deux refusent, et la sonde concluait que `strict` n'était pas
    # appliqué. Elle mesurait une différence qui n'existe plus.
    # Le témoin repose désormais sur les `options`, qui restent inertes sans `strict`
    # (mesuré le 08/09 : 181 tableaux du parc en portent sans les faire respecter).
    ("strict",
     {"strict": True, "fields": [{"key": "x", "type": "enum", "options": ["a"]}]},
     {"x": "b"},
     ({"fields": [{"key": "x", "type": "enum", "options": ["a"]}]}, {"x": "b"})),
    ("lifecycle",
     {"fields": [{"key": "s", "role": "status",
                  "lifecycle": {"states": ["a", "b"]}}]},
     {"s": "z"}, None),
    # oto#75 : la clé qui a vécu trois schémas de production SANS lecteur. Sa
    # sonde est donc la première chose qu'un client peut opposer au serveur qui
    # lui répond — « ce déploiement l'exécute-t-il, ou est-ce encore une
    # déclaration qui ne contraint rien ? »
    ("required_layers",
     {"fields": [{"key": "x", "required_layers": ["comment"]}]},
     {"x": "une valeur nue"}, None),
)

_ENFORCED: Optional[tuple] = None


def reset_enforced_keys() -> None:
    """Oublie le relevé mémorisé — pour un banc qui désarme une règle et vérifie que
    l'annonce tombe avec elle."""
    global _ENFORCED
    _ENFORCED = None


def enforced_keys() -> list[str]:
    """Les clés de validation que CETTE version EXÉCUTE, triées.

    Rendue à la pose ET à la lecture d'un schéma : un client peut donc vérifier que ce
    qu'il déclare sera appliqué par le serveur qui lui répond — c'est la seule parade
    au décalage entre le code écrit et la version servie."""
    global _ENFORCED
    if _ENFORCED is None:
        vues = []
        for cle, schema, row, temoin in _ENFORCEMENT_PROBES:
            if not validate_row(schema, row):
                continue                      # la règle n'existe pas ici
            if temoin and validate_row(temoin[0], temoin[1]):
                continue                      # elle refuse même sans la clé : pas elle
            vues.append(cle)
        # `key_required` (#516) ne se prouve pas sur une ROW : il se juge contre le
        # CONTENU du tableau (cette clé désigne-t-elle une ligne ?), que `validate_row`
        # ne voit pas. Sa sonde interroge donc la fonction qui DÉCIDE — dérivée du
        # code comme les autres, jamais une ligne de liste : le jour où le cran
        # disparaît, l'annonce tombe avec lui.
        if key_required_of({"key": "x", "key_required": True}):
            vues.append("key_required")
        # #586/#606 : les champs que l'appelant n'écrit pas se jugent sur le GESTE
        # (payload + ligne en place), pas sur une row seule — même sonde que
        # `key_required` : on interroge la fonction qui décide.
        if reserved_refusals({"fields": [{"key": "x", "readonly": True}]},
                             {"x": "b"}, {"x": "a"})[0]:
            vues.append("readonly")
        # oto#83 : le cran ne mord que sur la face agent — la sonde le dit donc
        # explicitement (`agent=True`), sinon elle mesurerait l'absence de contexte
        # d'appel et annoncerait « pas appliqué » sur un déploiement qui l'applique.
        if reserved_refusals({"fields": [{"key": "x", "agent_access": "none"}]},
                             {"x": "v"}, agent=True)[0]:
            vues.append("agent_access")
        # #614/#678 : le refus de la colonne non déclarée au premier niveau. Il ne se
        # prouve pas sur `validate_row` (le relevé vit hors d'elle, dans `_check_row`,
        # pour rester la source unique du « hors du référentiel ») — sa sonde
        # interroge donc la fonction qui décide, comme `key_required`.
        if off_schema_refusal({"strict": True, "unknown_fields": "reject",
                               "fields": [{"key": "x"}]}, {"inventée": "v"})[0]:
            vues.append("unknown_fields")
        # #517 : le périmètre de réservation se juge au PICK, pas sur une row — la
        # sonde interroge la fonction qui produit les clauses que le pick ajoute.
        if claimable.clauses(claimable.perimetre_of({"claimable": {"x": "1"}})):
            vues.append("claimable")
        _ENFORCED = tuple(sorted(vues))
    return list(_ENFORCED)


# ── Ce que le code LIT (#316) — la garde de la déclaration ───────────────────


def _read_keys() -> frozenset:
    """Les clés que le code LIT réellement, dérivées de son source.

    La GARDE de la déclaration par niveau (`schema_keys.ADMISES`), jamais son
    fondement : une clé lue ici et non déclarée serait REFUSÉE à la pose alors que le
    code s'en sert — `tests/test_schema_keys_oto56.py` l'interdit. La dérivation
    surestime (elle ramasse aussi des clés de ligne ou de datastore, `data`,
    `owner_id`…) et ne voit aucun NIVEAU : c'est exactement pourquoi elle ne peut pas
    fonder un refus.
    """
    import ast
    import pathlib

    keys: set = set()
    ici = pathlib.Path(__file__).parent
    # ⚠️ La liste est celle des FICHIERS, jamais celle des clés : un module qui se met
    # à lire un attribut de colonne doit être ajouté ici, sinon la garde ne le voit
    # pas et une clé lue pourrait n'être déclarée nulle part — donc refusée. `acces_agent.py`
    # (oto#83) lit `agent_access` par sa constante — c'est le cas qui l'a imposée.
    #
    # ⚠️ **Le second cas est la COUPE de `schema.py`** : les douze modules ci-dessous en
    # sont issus, et le jour où ils ont été écrits `schema.py` a cessé de contenir la
    # moindre lecture d'attribut. Le dérivé n'y voyait plus rien : c'est exactement le
    # défaut que le paragraphe au-dessus annonce, déclenché par un déplacement qui ne
    # changeait aucun comportement. `schema.py` reste listé — il ne coûte rien et il
    # redeviendrait porteur si quoi que ce soit y revenait.
    #
    # ⚠️ **Le troisième cas est la COUPE de `core.py`** (07/09/2026) : les sept
    # greffons ci-dessous en sont issus, et le noyau qui reste n'y a gardé qu'une
    # poignée de lectures. Le même déplacement pur, le même dérivé qui rétrécit —
    # d'où les sept noms ajoutés le jour même de la coupe.
    for nom in ("schema.py", "core.py", "acces_agent.py",
                "couches.py", "motifs.py", "declaration.py", "cycle_de_vie.py",
                "hors_schema.py", "champs_reserves.py", "definition.py",
                "couches_exigees.py", "validation.py", "phrases_de_refus.py",
                "effacements.py",
                "outils.py", "controles.py", "registre.py", "lecture.py",
                "ecriture.py", "ecriture_par_id.py", "lots.py", "file_de_travail.py",
                "vocabulaire.py", "non_applique.py", "formule.py", "reglages.py"):
        try:
            arbre = ast.parse((ici / nom).read_text(encoding="utf-8"))
        # noqa: SILENT — clés de schéma illisibles ⇒ ensemble vide, la lecture continue
        except Exception:      # source illisible (zip, .pyc seul) : on n'invente pas
            return frozenset()
        # Les constantes de MODULE (`CLE = "agent_access"`), pour résoudre
        # `f.get(CLE)` comme `f[CLE]` : sans elles, une clé lue par sa constante — la
        # forme qu'on encourage justement pour ne pas répéter un littéral — passe pour
        # jamais lue, et une garde bâtie dessus l'accuserait d'être morte. C'est ce
        # qui est arrivé à `flat_alias` (retirée depuis) avant que les deux formes ne
        # soient résolues ici ; les DEUX comptent, n'en résoudre qu'une laisse le trou
        # ouvert sur l'autre.
        constantes = {
            n.targets[0].id: n.value.value
            for n in arbre.body
            if isinstance(n, ast.Assign) and len(n.targets) == 1
            and isinstance(n.targets[0], ast.Name)
            and isinstance(n.value, ast.Constant) and isinstance(n.value.value, str)}
        for n in ast.walk(arbre):
            if (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                    and n.func.attr == "get" and n.args):
                arg = n.args[0]
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    keys.add(arg.value)
                elif isinstance(arg, ast.Name) and arg.id in constantes:
                    keys.add(constantes[arg.id])
            if isinstance(n, ast.Subscript) and isinstance(n.slice, ast.Name) \
                    and n.slice.id in constantes:
                keys.add(constantes[n.slice.id])
    return frozenset(keys)


_READ_KEYS: Optional[frozenset] = None


def interpreted_keys() -> frozenset:
    """Le vocabulaire effectivement interprété — calculé une fois, dérivé du code."""
    global _READ_KEYS
    if _READ_KEYS is None:
        _READ_KEYS = _read_keys()
    return _READ_KEYS
