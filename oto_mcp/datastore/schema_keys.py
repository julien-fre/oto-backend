"""Ce qu'un schéma de tableau peut porter — **la déclaration**, une seule, par niveau.

Le validateur acceptait n'importe quelle clé. `readonly` passe, `editable` passe,
`zorglub` passe. Le cas fondateur (oto#56, signal 658) est un agent qui pose
`readonly: true` **et** `editable: true` en espérant que le second rouvre le premier
pour un humain — `editable` n'existe nulle part, il a été accepté **parce que rien ne
regardait**. ⚠️ Le cas grave est l'autre : qui écrit `read_only` au lieu de `readonly`
croit avoir verrouillé sa colonne et n'a rien verrouillé.

## Le vocabulaire est FERMÉ (01/10/2026)

Pendant un mois, une clé inconnue n'a valu qu'un avertissement — et l'avertissement ne
suffisait pas : mesuré le 01/10 sur 442 tableaux à schéma, 45 portaient des clés que
personne ne lisait (`labels` sur une colonne, `enum` pour `options`, `note`, `editable`,
`depends_on`…). Désormais une clé qu'un niveau n'admet pas est REFUSÉE à la pose comme
au patch (`cles_inconnues.refus`, appelé par `validate_schema_def`), et ce qu'un
consommateur veut ranger dans un schéma va dans `meta` — la zone libre, transportée,
jamais lue, bornée en taille.

## Pourquoi une déclaration, et pas « ce que le validateur lit »

Le schéma n'est pas seulement validé, il est **servi** : c'est un contrat que le
dashboard lit. Une liste dérivée des `.get()` du validateur manquait `label` (lu par
tous les écrans) et en comptait trop (`strict` ou `states` sur une colonne passaient,
lus à un AUTRE niveau). Chaque attribut dit donc **qui le lit** : le validateur, le
front, ou personne (`meta`, qui le dit dans sa description).

⚠️ Les clés `front` sont confrontées au dashboard CÔTÉ DASHBOARD
(`schema-keys-check.mjs`), contre la déclaration servie sur
`GET /api/datastore/schema/keys`. La moitié `validateur` est gardée ici :
`tests/test_schema_keys_oto56.py` exige que **tout ce que le validateur lit soit
déclaré**, à chaque niveau — sinon le refus tomberait sur une clé lue.
"""
from __future__ import annotations

import json
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


#: La ZONE LIBRE (01/10/2026). Un objet, admis à chaque niveau, transporté tel quel et
#: jamais lu par la plateforme : c'est là qu'un consommateur range SES déclarations
#: (`depends_on`, `explained_by`, des libellés de valeurs…) au lieu d'inventer une clé
#: à côté de celles qu'oto interprète. Le vocabulaire est fermé ; `meta` est la porte.
#: Seule sa TAILLE est bornée, sérialisée en JSON compact (UTF-8) : un schéma est relu
#: à chaque écriture de ligne, une annotation n'a pas à le faire grossir sans limite.
META = "meta"
META_MAX_OCTETS = 4096


def taille_json(valeur) -> int:
    """La mesure de la borne : la taille d'une valeur en JSON compact, en octets UTF-8."""
    return len(json.dumps(valeur, ensure_ascii=False, separators=(",", ":"))
               .encode("utf-8"))

_CLE_META = Cle(META, (), "zone libre : un objet transporté tel quel, jamais lu — SANS "
                f"EFFET sur la plateforme ; au plus {META_MAX_OCTETS} octets en JSON")


#: LA déclaration du niveau COLONNE. Ajouter un attribut au schéma passe par cette
#: liste — sans elle, le refus des clés inconnues le rejette.
CLES: tuple[Cle, ...] = (
    # — structure, lues des deux côtés —
    Cle("key", ("validateur", "front"), "le nom de la colonne (ou `colonne.couche`)"),
    Cle("type", ("validateur", "front"),
        "le type de la valeur : " + " | ".join(SCALAR_TYPES + COMPOSITE_TYPES)),
    Cle("of", ("validateur", "front"), "le type des éléments d'une liste", True),
    Cle("fields", ("validateur", "front"), "les sous-champs d'un objet", True),
    # — crans de garde, lus par le validateur —
    Cle("readonly", ("validateur", "front"),
        "colonne du fichier source : une valeur posée ne se modifie plus, une case "
        "vide (absente, null ou \"\") se remplit", True),
    # oto#34 : `agent_access`, `required_layers` et `formula` ne sont PAS lues par le
    # front — le dashboard ne les rend pas (`schema-keys-check.mjs`, remesure du
    # 01/10/2026). Les y déclarer promettait un lecteur qui n'existe pas.
    Cle("agent_access", ("validateur",),
        "à qui la colonne est servie : \"write\" (défaut), \"read\" (un agent la voit, "
        "n'écrit pas sa valeur), \"none\" (un agent ne la voit pas du tout)", True),
    Cle("max_length", ("validateur", "front"), "borne de longueur, publiée dans le contrat"),
    Cle("pattern", ("validateur",), "forme exigée de la valeur"),
    Cle("required_when", ("validateur", "front"), "obligatoire sous condition"),
    # oto#75 barreau 1. ⚠️ Elle a vécu TROIS schémas de production sans aucun
    # lecteur : posée, servie, et sans effet — l'auteur croyait la provenance
    # exigée. C'est le cas fondateur de ce fichier, à un cran de plus : ici la
    # clé était bien orthographiée, et personne ne la lisait.
    Cle("required_layers", ("validateur",),
        "les couches sans lesquelles une valeur non vide ne s'écrit pas "
        "(`[\"comment\"]`) — la provenance voyage avec la valeur", True),
    Cle("lifecycle", ("validateur", "front"),
        "états et transitions permises — ses clés : `CLES_DU_CYCLE`"),
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
    Cle("formula", ("validateur",),
        "le texte OpenFormula d'une colonne calculée (`type: \"formula\"`)", True),
    Cle("max_items", ("validateur", "front"), "nombre maximum d'éléments d'une liste"),
    # — présentation, lues par le FRONT SEUL : invisibles au validateur, et c'est
    #   exactement ce qui a fait échouer la première forme de ce lot —
    Cle("label", ("front",), "le nom affiché de la colonne (le plus lu de tous)"),
    # ⚠️ **Le seul texte d'aide.** `note`, `help`, `hint` et `placeholder` y ont été
    # REPLIÉS (01/10/2026, `scripts/durcir_schemas.py`) : quatre noms pour un même
    # geste, dont aucun n'était lu par un écran — le dashboard ne lit aucun des
    # quatre, et un agent qui cherchait « l'aide de cette colonne » devait deviner
    # laquelle était posée. Ils sont désormais REFUSÉS avec un renvoi ici.
    Cle("description", ("front",),
        "le texte long de la colonne — son aide, sa consigne de saisie : c'est LE "
        "texte d'aide (`note`, `help`, `hint`, `placeholder` y sont repliés)"),
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
    _CLE_META,
)

#: Les clés qui n'ont de sens que sur une colonne, jamais sur une couche. Le validateur
#: s'en sert pour refuser `colonne.comment: {readonly: true}` — c'est ce qui fait de
#: cette déclaration le premier client de sa propre liste, et pas une documentation.
COLONNE_SEULEMENT: tuple[str, ...] = tuple(c.nom for c in CLES if c.colonne_seulement)

#: Ce que le validateur consulte réellement au niveau colonne. Le banc de garde exige
#: que chaque clé déclarée « validateur » soit lue par le code — sinon la liste servie
#: promettrait un cran qui n'existe pas.
LUES_PAR_LE_VALIDATEUR: frozenset[str] = frozenset(
    c.nom for c in CLES if "validateur" in c.lecteurs)

#: La moitié que RIEN ne peut dériver ici : elle est lue dans un autre dépôt. C'est
#: exactement ce qui manquait au vocabulaire dérivé du code, et ce qui lui faisait
#: dénoncer `label` sur presque tous les tableaux.
LUES_PAR_LE_FRONT: frozenset[str] = frozenset(
    c.nom for c in CLES if "front" in c.lecteurs)


# ── L'INTÉRIEUR du bloc `lifecycle` (oto#140) ─────────────────────────────────
#
# Même parti que `CLES` un cran plus bas : ce qu'un cycle de vie peut porter, et
# surtout QUI le lit. La liste n'existait pas — chaque clé du bloc n'était écrite que
# dans le module qui la consomme. Elle naît avec `labels`, la première clé du bloc qui
# n'est lue QUE par un front : sans la dire ici, rien ne distinguerait « présentation »
# de « oubliée par le validateur », exactement la confusion qui a failli coûter
# `label`, `help` et `hint` au niveau colonne.
CLES_DU_CYCLE: tuple[Cle, ...] = (
    Cle("states", ("validateur", "front"), "les états permis de la colonne"),
    Cle("transitions", ("validateur", "front"),
        "`{état: [états atteignables]}` — une LISTE, même pour une seule destination ; "
        "une transition non déclarée est refusée"),
    Cle("terminal", ("validateur", "front"),
        "les états finaux ; à défaut, dérivés (un état sans sortie en est un)"),
    Cle("max_claims", ("validateur", "front"),
        "plafond de réservations SANS écriture avant l'abandon (#433)"),
    Cle("abandon_state", ("validateur", "front"),
        "l'état terminal où le plafond verse une ligne (#433)"),
    Cle("claimable", ("validateur", "front"),
        "le périmètre que la file sert, grammaire de `filter` (#517)"),
    # oto#95 — la contrepartie d'`abandon_state` : la plateforme fait RECULER une ligne
    # réservée sans écriture, elle fait AVANCER celle qu'on a écrite. Lue par le
    # validateur (à la pose) et par le relâchement (`db/rowavance.py`) ; le dashboard
    # ne la lit pas encore — la déclarer « front » promettrait un lecteur absent.
    Cle("advance", ("validateur",),
        "`{état: état suivant}` — la suite des passes : relâchée après une écriture "
        "réussie depuis sa réservation, une ligne passe à l'état suivant (oto#95) ; "
        "chaque pas est une transition déclarée, jamais depuis un état terminal"),
    # ⚠️ PRÉSENTATION, JAMAIS VALIDATION. Sa FORME est jugée à la pose (un objet, des
    # clés qui sont des états déclarés, des chaînes non vides d'au plus
    # `cycle_de_vie.LIBELLE_ETAT_MAX` caractères) ; aucune écriture de ligne ne la lit,
    # et une ligne porte toujours le CODE de l'état, jamais son libellé.
    Cle("labels", ("front",),
        "`{état: \"libellé\"}` — le nom affiché de chaque étape ; présentation, "
        "jamais validation : aucune écriture ne le lit"),
    _CLE_META,
)


# ── La TÊTE du schéma (#97) ──────────────────────────────────────────────────
#
# ⚠️ **Le contrôle des clés inconnues ne parcourait que les COLONNES.** Un réglage de
# tête mal orthographié — `stricte` pour `strict`, alors réglage de tête — passait donc en silence complet, et
# la conséquence est la plus large du datastore : `validation_active` rend `False`, donc
# **toutes les gardes du tableau tombent d'un coup** pendant que son propriétaire les
# croit armées. Ce n'est pas une protection qui s'affaiblit, ce sont toutes.
#
# Mesuré sur le parc avant d'écrire cette liste — c'est ce qui la rend sûre plutôt que
# devinée : sur 362 tableaux à schéma, **deux clés de tête seulement** sortent de ce que
# le code lit, `description` (15 tableaux) et `semantic_search` (1). La déclaration
# ci-dessous couvre donc l'existant légitime (`semantic_search`, un paramètre d'appel,
# est refusé avec sa propre phrase).

CLES_DE_TETE: tuple[Cle, ...] = (
    Cle("fields", ("validateur", "front"), "les colonnes du tableau"),
    Cle("key", ("validateur", "front"), "la colonne qui sert de clé métier"),
    # oto#127 (02/10/2026) : DEUX réglages, un par axe (`reglages.py`). Ils remplacent
    # `strict`, `unknown_fields` et `key_required`, désormais refusés à la pose et au
    # patch avec leur équivalent exact (`reglages.refus_anciens`).
    Cle("unknown_columns", ("validateur",),
        "le sort d'une colonne non déclarée — `\"create\"` (défaut : créée en "
        "silence), `\"report\"` (créée et nommée dans `hors_schema`) ou "
        "`\"reject\"` (refusée) ; hors `create`, le format fait contrat"),
    Cle("new_rows", ("validateur",),
        "le droit d'une ligne nouvelle de naître — `\"create\"` (défaut) ou "
        "`\"reject\"` (une écriture qui ne désigne aucune ligne existante est "
        "refusée ; exige `key`)"),
    # ⚠️ Lue par personne CÔTÉ SERVEUR, et gardée quand même : 15 tableaux la portent,
    # le schéma est servi tel quel, donc un écran peut l'afficher. « oto ne l'interprète
    # pas » n'est pas « personne ne la lit » — la leçon des six attributs portés comme
    # morts dont un seul l'était.
    Cle("description", ("front",), "la description du tableau, servie telle quelle"),
    _CLE_META,
)

#: ⚠️ Des PARAMÈTRES de `data_set_schema`, jamais des clés de schéma. Posés dans le
#: schéma ils sont stockés, servis, et **sans effet** — l'auteur croit avoir réglé
#: quelque chose. Mesuré : un tableau du parc porte `semantic_search` dans son schéma et
#: n'a donc pas la recherche sémantique qu'il croit avoir activée. Ils méritent leur
#: propre phrase : « ce n'est pas une clé de schéma, c'est un paramètre de l'appel » se
#: corrige en un geste, « clé inconnue » fait chercher une faute de frappe.
PARAMETRES_HORS_SCHEMA: frozenset[str] = frozenset({"semantic_search", "datastore",
                                                    "namespace", "owner"})


# ── Les CINQ niveaux d'un schéma (01/10/2026) ─────────────────────────────────
#
# Un schéma n'a pas un vocabulaire, il en a cinq : sa tête, ses colonnes, les
# sous-champs d'un objet (ou d'un élément de liste), l'élément d'une liste (`of`) et
# le bloc `lifecycle`. Le relevé des clés inconnues n'en regardait que deux (la tête
# et les colonnes), et il les jugeait contre une liste DÉRIVÉE des `.get()` du code —
# qui en comptait trop : `strict`, `states` ou `terminal` posés sur une colonne
# passaient en silence, parce qu'ils sont lus… à un autre niveau.
#
# Chaque niveau DÉCLARE donc ce qu'il admet, et c'est cette déclaration — jamais la
# dérivation — qui fonde le refus (`cles_inconnues.refus`).

#: Ce qu'une colonne admet et qu'un sous-champ n'admet pas : sous un sous-record, ni le
#: verrou (`readonly`), ni l'accès agent, ni le cycle de vie, ni la formule, ni le titre
#: de ligne ne sont lus — la garde ne descend pas, l'écran non plus.
PREMIER_NIVEAU_SEULEMENT: tuple[str, ...] = (
    "readonly", "agent_access", "lifecycle", "formula", "display")

#: Un sous-champ — `fields` d'une colonne `object`, ou `of.fields` d'une liste de
#: sous-records. DÉRIVÉ des colonnes : un attribut ajouté à une colonne descend seul.
CLES_DE_SOUS_CHAMP: tuple[Cle, ...] = tuple(
    c for c in CLES if c.nom not in PREMIER_NIVEAU_SEULEMENT)

_COLONNE = {c.nom: c for c in CLES}

#: L'élément d'une liste (`of`). Le validateur n'en lit que le type, les options et
#: les sous-champs (`validation._type_error`) : une borne ou un motif posés ICI ne
#: contraignent rien — ils vont sur un sous-champ.
CLES_D_ELEMENT: tuple[Cle, ...] = (
    Cle("key", ("validateur", "front"),
        "le sous-champ qui identifie un élément d'une liste de sous-records"),
    _COLONNE["type"], _COLONNE["fields"], _COLONNE["of"], _COLONNE["options"],
    _COLONNE["label"], _COLONNE["description"], _CLE_META,
)

#: Les cinq niveaux, dans l'ordre où un schéma se lit. Les CLÉS de ce dict sont les
#: noms internes ; `NIVEAUX_SERVIS` donne ceux du contrat REST.
NIVEAUX: dict[str, tuple[Cle, ...]] = {
    "tete": CLES_DE_TETE,
    "champ": CLES,
    "sous_champ": CLES_DE_SOUS_CHAMP,
    "element": CLES_D_ELEMENT,
    "cycle": CLES_DU_CYCLE,
}

#: Ce que chaque niveau admet — LA référence du refus et du script de migration.
ADMISES: dict[str, frozenset[str]] = {
    n: frozenset(c.nom for c in cles) for n, cles in NIVEAUX.items()}

#: Ce que la LECTURE COMPACTE garde à chaque niveau (oto#35) : les clés que le
#: VALIDATEUR lit — la structure et les contraintes. Tout ce qui n'est lu que par un
#: écran (`label`, `description`, `hidden`, `width`, `role`, les `labels` d'un cycle) ou
#: par personne (`meta`) en sort. DÉRIVÉ des lecteurs déclarés, jamais listé à part : une
#: clé ajoutée au vocabulaire entre ou sort de la lecture compacte avec sa déclaration.
CONTRAINTES: dict[str, frozenset[str]] = {
    n: frozenset(c.nom for c in cles if "validateur" in c.lecteurs)
    for n, cles in NIVEAUX.items()}

#: Où l'on est, dans une phrase de refus : « `x` n'est pas admise <ici> ».
NOMS_DE_NIVEAU: dict[str, str] = {
    "tete": "en tête du schéma",
    "champ": "sur une colonne",
    "sous_champ": "sur un sous-champ",
    "element": "sur l'élément d'une liste (`of`)",
    "cycle": "dans un bloc `lifecycle`",
}

#: Le nom d'un niveau sur le contrat REST (`GET /api/datastore/schema/keys`).
NIVEAUX_SERVIS: dict[str, str] = {
    "tete": "head", "champ": "field", "sous_champ": "subfield",
    "element": "item", "cycle": "lifecycle",
}

#: Les fautes qui MÉRITENT d'être nommées : une clé inconnue proche d'une clé admise
#: n'est presque jamais une déclaration délibérée. Le cas fondateur (#316) : trois champs
#: posés avec `enum: [...]` au lieu d'`options: [...]`, et 504 valeurs libres sur un
#: tableau qui se croyait contraint. Une correction n'est proposée que si sa cible est
#: admise au niveau de la faute.
FAUTES_CONNUES: dict[str, str] = {
    "enum": "options", "enums": "options", "option": "options",
    "choices": "options", "choix": "options", "values": "options",
    "valeurs": "options", "allowed": "options",
    "maxlength": "max_length", "max_len": "max_length", "maxLength": "max_length",
    "requiredWhen": "required_when", "required_if": "required_when",
    "mandatory": "required", "obligatoire": "required",
    "champs": "fields", "columns": "fields",
    "cle": "key", "name": "key", "nom": "key",
    "read_only": "readonly", "readOnly": "readonly", "writable_by": "readonly",
}

#: Quatre noms pour un même geste, repliés dans `description` le 01/10/2026 et
#: refusés depuis, avec un renvoi vers elle. L'ORDRE est celui du repli.
TEXTES_D_AIDE: tuple[str, ...] = ("note", "help", "hint", "placeholder")

#: Des clés qui ont EXISTÉ et que plus rien ne lit : leur refus dit pourquoi, et où
#: est passé ce qu'elles faisaient — « clé inconnue » ferait chercher une faute de
#: frappe sur un mot qui était juste.
CLES_RETIREES: dict[str, str] = {
    "origine": ("retirée le 08/09/2026, elle n'est plus lue : l'origine d'une valeur "
                "se déclare par l'appel qui APPORTE la donnée "
                "(`data_write(donnees_d_origine=true)`), plus par le schéma"),
}


def _entree(c: Cle) -> dict:
    return {"key": c.nom, "readers": list(c.lecteurs), "what": c.quoi,
            "column_only": c.colonne_seulement}


def servie() -> dict:
    """La déclaration, telle qu'elle part sur la face REST.

    `keys` est le niveau COLONNE (le contrat que le dashboard confronte depuis le
    06/09, `schema-keys-check.mjs`) ; `levels` sert les CINQ niveaux, colonne
    comprise. Servie plutôt que gardée en Python pour qu'un front confronte ce qu'il
    lit à ce qui est déclaré."""
    return {"keys": [_entree(c) for c in CLES],
            "levels": {NIVEAUX_SERVIS[n]: [_entree(c) for c in cles]
                       for n, cles in NIVEAUX.items()},
            "meta_max_bytes": META_MAX_OCTETS}
