"""Les attributs qu'une colonne de schéma peut porter — **la déclaration**, une seule.

Le validateur acceptait n'importe quelle clé. `readonly` passe, `editable` passe,
`zorglub` passe : aucune n'est refusée, aucune n'est signalée. Le cas fondateur
(oto#56, signal 658) est un agent qui pose `readonly: true` **et** `editable: true` en
espérant que le second rouvre le premier pour un humain — `editable` n'existe nulle
part, il n'a donc pas été « accepté puis ignoré » par une implémentation partielle, il a
été accepté **parce que rien ne regardait**.

⚠️ **Le cas grave est l'autre** : qui écrit `read_only` au lieu de `readonly` croit
avoir verrouillé sa colonne et n'a rien verrouillé. La faute de frappe est silencieuse
**et** elle désarme le cran. Elle ne se découvre qu'à la première écriture qui passe là
où on croyait un verrou.

## Pourquoi une déclaration, et pas « ce que le validateur lit »

Première idée, écartée **par la mesure** : dériver la liste en observant le validateur.
Elle est fausse, et de peu — le schéma n'est pas seulement validé, il est **servi**.
C'est un contrat que le dashboard et les fronts tiers lisent. Cinq attributs vivants y
échappaient (`label`, lu 40 fois côté dashboard, `help`, `placeholder`, `hint`,
`description`) : un avertissement bâti là-dessus aurait crié « `label` n'est lue par
personne » sur presque tous les tableaux existants. Un faux positif dans un signal de
qualité est pire que pas de signal — on apprend à l'ignorer, et il ne sert plus le jour
où il a raison.

D'où la forme retenue : **une déclaration, deux clients.** Le validateur en est le
premier (il en dérive ses crans de niveau colonne), l'avertissement le second. Rien
n'est recopié, et ce qui manquait cruellement est écrit ici : **qui lit quoi.**

⚠️ **Les clés `front` sont déclarées à la main, et c'est une dette assumée.** Rien ne
vérifie aujourd'hui que le dashboard lit bien celles-là et rien d'autre. Le palier
suivant — pas ce lot — est un contrôle CÔTÉ DASHBOARD qui confronte les clés qu'il lit à
cette déclaration ; c'est pour ça qu'elle est **servie** (`GET /api/datastore/schema/
keys`) plutôt que gardée en Python.

⚠️ Ce qui garde la moitié `validateur`, en revanche, est mécanique :
`tests/test_schema_keys_oto56.py` observe le validateur et exige que **tout ce qu'il lit
soit déclaré ici**. Un `f.get("nouveau")` ajouté sans déclaration rougit avant que
l'avertissement ne se mette à mentir.
"""
from __future__ import annotations

from dataclasses import dataclass

from .declaration import COMPOSITE_TYPES, SCALAR_TYPES


@dataclass(frozen=True)
class Cle:
    """Un attribut de colonne, et surtout : **qui le lit**.

    `lecteurs` ⊆ {"validateur", "front"}. Une clé lue par le seul front est
    parfaitement légitime — c'est le fait que personne ne l'écrivait qui a coûté."""
    nom: str
    lecteurs: tuple[str, ...]
    quoi: str
    #: `True` = n'a de sens que sur une COLONNE, jamais sur une couche
    #: (`colonne.comment`). C'est ce que le validateur consomme ici.
    colonne_seulement: bool = False


#: LA déclaration. Ajouter un attribut au schéma passe par cette liste — c'est ce qui
#: rend l'avertissement vrai, et c'est aussi ce qui le rend maintenable.
CLES: tuple[Cle, ...] = (
    # — structure, lues des deux côtés —
    Cle("key", ("validateur", "front"), "le nom de la colonne (ou `colonne.couche`)"),
    Cle("type", ("validateur", "front"),
        "le type de la valeur : " + " | ".join(SCALAR_TYPES + COMPOSITE_TYPES)),
    Cle("of", ("validateur", "front"), "le type des éléments d'une liste", True),
    Cle("fields", ("validateur", "front"), "les sous-champs d'un objet", True),
    # — crans de garde, lus par le validateur —
    Cle("readonly", ("validateur", "front"),
        "colonne du fichier source : une écriture ne la change pas", True),
    Cle("agent_access", ("validateur", "front"),
        "à qui la colonne est servie : \"write\" (défaut), \"read\" (un agent la voit, "
        "n'écrit pas sa valeur), \"none\" (un agent ne la voit pas du tout)", True),
    # ⚠️ Lue par PERSONNE depuis le 08/09/2026 — et c'est pour ça qu'elle est déclarée
    # avec un tuple VIDE plutôt que retirée de la liste. Retirer la ligne la ferait
    # passer pour une clé inconnue ; la garder sans lecteur la fait nommer pour ce
    # qu'elle est : un attribut que 500 colonnes portent et que la plateforme n'applique
    # plus. Le cran est remplacé par `donnees_d_origine`, déclaré à l'import.
    Cle("origine", (), "SANS EFFET — le cran est remplacé par `donnees_d_origine`, "
        "déclaré à l'import ; cet attribut n'est plus lu", True),
    Cle("max_length", ("validateur", "front"), "borne de longueur, publiée dans le contrat"),
    Cle("pattern", ("validateur",), "forme exigée de la valeur"),
    Cle("required_when", ("validateur", "front"), "obligatoire sous condition"),
    # oto#75 barreau 1. ⚠️ Elle a vécu TROIS schémas de production sans aucun
    # lecteur : posée, servie, et sans effet — l'auteur croyait la provenance
    # exigée. C'est le cas fondateur de ce fichier, à un cran de plus : ici la
    # clé était bien orthographiée, et personne ne la lisait.
    Cle("required_layers", ("validateur", "front"),
        "les couches sans lesquelles une valeur non vide ne s'écrit pas "
        "(`[\"comment\"]`) — la provenance voyage avec la valeur", True),
    Cle("lifecycle", ("validateur", "front"), "états et transitions permises"),
    # ⚠️ `enum` a été RETIRÉE d'ici : elle n'était lue par personne. C'est une VALEUR
    # de `type` (`"type": "enum"`), jamais une clé — et la table des fautes de frappe
    # la traite comme une erreur à corriger (`enum` → `options`). Cette liste est
    # SERVIE sur `GET /api/datastore/schema/keys` : elle prescrivait donc, à qui la
    # lit avant d'écrire, exactement ce que le validateur corrige ensuite. Trois
    # tableaux de production (9 454 lignes) portent l'`enum` résiduel qu'elle
    # légitimait, à côté de l'`options` qui fait foi.
    Cle("options", ("validateur", "front"),
        "les valeurs permises d'une colonne `type: \"enum\"` — c'est ELLE qui contraint"),
    Cle("required", ("validateur", "front"), "la valeur ne peut pas être vide"),
    # oto-backend#1008 : le texte OpenFormula d'une colonne `type: "formula"` —
    # calculée par ligne, readonly implicite (cf. `readonly_fields`), recalculée à
    # l'écriture d'une colonne dont elle dépend et au backfill quand elle est posée.
    Cle("formula", ("validateur", "front"),
        "le texte OpenFormula d'une colonne calculée (`type: \"formula\"`)", True),
    Cle("max_items", ("validateur", "front"), "nombre maximum d'éléments d'une liste"),
    # — présentation, lues par le FRONT SEUL : invisibles au validateur, et c'est
    #   exactement ce qui a fait échouer la première forme de ce lot —
    Cle("label", ("front",), "le nom affiché de la colonne (le plus lu de tous)"),
    Cle("description", ("front",), "le texte long de la colonne"),
    Cle("help", ("front",), "l'aide affichée à la saisie"),
    Cle("hint", ("front",), "l'indice court à côté du champ"),
    Cle("placeholder", ("front",), "le texte fantôme d'un champ vide"),
    # ⚠️ **`hidden` et `width` manquaient, et leur absence coûtait 87 % du bruit du
    # canal d'avertissement.** Mesuré le 10/09/2026 : sur 363 tableaux à schéma,
    # **224 (61 %) portaient un avertissement à la lecture** — et `hidden` (186) plus
    # `width` (141) en expliquaient presque tout. Sans elles : **30 tableaux, 8 %**.
    #
    # Or la description de `data_set_schema` les PRESCRIT, mot pour mot : « `width`…
    # **declare it** to keep a stable layout », « `hidden: true`… **Use it** for opaque
    # ids and technical fields ». Le produit disait donc « déclare-la », puis dénonçait
    # la déclaration à chaque lecture, sur 194 tableaux. La plateforme criait sur sa
    # propre consigne.
    #
    # ⚠️ Et le coût n'est pas le bruit : c'est le SIGNAL qu'il couvrait. Les 26
    # tableaux portant `enum` là où `options` fait foi — la clé même qui a laissé
    # passer 504 valeurs libres — étaient noyés dans 194 faux positifs.
    #
    # Vérifié dans `oto-dashboard` avant de les déclarer, parce qu'une clé « front »
    # qui ne serait lue par personne serait la même faute dans l'autre sens :
    # `hidden` filtre les cartes (`DatastoreCards.vue`) et pilote la sauvegarde de vue
    # (`DatastoreTable.vue`) ; `width` est lu par `RowDrawer.vue` — « déclarée au
    # schéma (`width`) sinon dérivée du widget » — et porté par `datastoreForm.ts`.
    # Elles sont donc exactement dans le cas de `label` : interprétées par le
    # consommateur auquel elles s'adressent, jamais par le validateur.
    Cle("hidden", ("front",),
        "garde la colonne hors des colonnes du tableau, par défaut"),
    Cle("width", ("front",), "la largeur du champ dans la fiche — `half` ou `full`"),
    Cle("display", ("validateur", "front"), "comment la colonne se rend", True),
    # ⚠️ `role` n'est plus lu par le validateur depuis le 08/09/2026 : `status` est
    # désigné par le bloc `lifecycle`, `title` par `display: "title"`. Il reste servi
    # et transporté fidèlement — un front l'interprète pour peindre ses colonnes — mais
    # oto n'en fait RIEN. Le déclarer « validateur » recommanderait d'écrire une clé
    # que la plateforme ignore, à quelqu'un qui lit cette liste justement pour savoir
    # quoi écrire.
    Cle("role", ("front",), "indication d'affichage, lue par un consommateur — oto "
        "ne l'interprète pas", False),
)

#: Tout ce qu'une colonne a le droit de porter. C'est CE nom que l'avertissement
#: consulte — jamais une liste recopiée à côté.
RECONNUES: frozenset[str] = frozenset(c.nom for c in CLES)

#: Les clés qui n'ont de sens que sur une colonne, jamais sur une couche. Le validateur
#: s'en sert pour refuser `colonne.comment: {readonly: true}` — c'est ce qui fait de
#: cette déclaration le premier client de sa propre liste, et pas une documentation.
COLONNE_SEULEMENT: tuple[str, ...] = tuple(c.nom for c in CLES if c.colonne_seulement)

#: Ce que le validateur consulte réellement. Le banc de garde exige que ce soit un
#: sous-ensemble de `RECONNUES` : une clé lue et non déclarée ferait mentir
#: l'avertissement, et personne ne s'en apercevrait avant qu'un utilisateur ne le
#: signale.
LUES_PAR_LE_VALIDATEUR: frozenset[str] = frozenset(
    c.nom for c in CLES if "validateur" in c.lecteurs)

#: La moitié que RIEN ne peut dériver ici : elle est lue dans un autre dépôt. C'est
#: exactement ce qui manquait au vocabulaire dérivé du code, et ce qui lui faisait
#: dénoncer `label` sur presque tous les tableaux. `schema.vocabulaire_vivant()` en
#: fait l'union avec le dérivé — une seule référence pour les deux avertissements.
LUES_PAR_LE_FRONT: frozenset[str] = frozenset(
    c.nom for c in CLES if "front" in c.lecteurs)


def servie() -> list[dict]:
    """La déclaration, telle qu'elle part sur la face REST.

    Servie plutôt que gardée en Python pour que le dashboard puisse un jour confronter
    ce qu'il lit à ce qui est déclaré — c'est le seul chemin qui rendra la moitié
    `front` aussi sûre que la moitié `validateur`."""
    return [{"key": c.nom, "readers": list(c.lecteurs), "what": c.quoi,
             "column_only": c.colonne_seulement} for c in CLES]


# ── La TÊTE du schéma (#97) ──────────────────────────────────────────────────
#
# ⚠️ **Le contrôle des clés inconnues ne parcourait que les COLONNES.** Un réglage de
# tête mal orthographié — `stricte` pour `strict` — passait donc en silence complet, et
# la conséquence est la plus large du datastore : `validation_active` rend `False`, donc
# **toutes les gardes du tableau tombent d'un coup** pendant que son propriétaire les
# croit armées. Ce n'est pas une protection qui s'affaiblit, ce sont toutes.
#
# Mesuré sur le parc avant d'écrire cette liste — c'est ce qui la rend sûre plutôt que
# devinée : sur 362 tableaux à schéma, **deux clés de tête seulement** sortent de ce que
# le code lit, `description` (15 tableaux) et `semantic_search` (1). La déclaration
# ci-dessous couvre donc l'existant légitime, et l'avertissement ne criera pas sur le
# régime normal — celui qu'on apprend à ignorer.

CLES_DE_TETE: tuple[Cle, ...] = (
    Cle("fields", ("validateur", "front"), "les colonnes du tableau"),
    Cle("key", ("validateur", "front"), "la colonne qui sert de clé métier"),
    Cle("strict", ("validateur",),
        "arme la validation : sans lui, `options`, `type` et les bornes ne "
        "contraignent rien"),
    Cle("key_required", ("validateur",),
        "une écriture qui ne désigne aucune ligne existante est refusée"),
    Cle("unknown_fields", ("validateur",),
        "le sort d'une colonne non déclarée — `\"report\"` (défaut) ou `\"reject\"`"),
    # ⚠️ Lue par personne CÔTÉ SERVEUR, et gardée quand même : 15 tableaux la portent,
    # le schéma est servi tel quel, donc un écran peut l'afficher. « oto ne l'interprète
    # pas » n'est pas « personne ne la lit » — la leçon des six attributs portés comme
    # morts dont un seul l'était.
    Cle("description", ("front",), "la description du tableau, servie telle quelle"),
)

#: Ce qu'une tête de schéma a le droit de porter.
TETE_RECONNUES: frozenset[str] = frozenset(c.nom for c in CLES_DE_TETE)

#: ⚠️ Des PARAMÈTRES de `data_set_schema`, jamais des clés de schéma. Posés dans le
#: schéma ils sont stockés, servis, et **sans effet** — l'auteur croit avoir réglé
#: quelque chose. Mesuré : un tableau du parc porte `semantic_search` dans son schéma et
#: n'a donc pas la recherche sémantique qu'il croit avoir activée. Ils méritent leur
#: propre phrase : « ce n'est pas une clé de schéma, c'est un paramètre de l'appel » se
#: corrige en un geste, « clé inconnue » fait chercher une faute de frappe.
PARAMETRES_HORS_SCHEMA: frozenset[str] = frozenset({"semantic_search", "datastore",
                                                    "namespace", "owner"})
