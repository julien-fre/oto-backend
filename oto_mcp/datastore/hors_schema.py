"""Une clé que la déclaration ne nomme pas — signalée en haut, REFUSÉE en dessous.

`strict` porte deux contrats sous un seul mot, et l'asymétrie est voulue (#294, #544,
#614/#678) :

- **au premier niveau**, une clé inconnue crée une vraie colonne — c'est un droit du
  contrat 0016, celui qui permet d'explorer un tableau avant de le typer. Elle est
  seulement SIGNALÉE (`off_schema_keys`, `off_schema_warning`), sauf si le tableau a
  fini d'être exploré et pose `unknown_fields: "reject"` (`unknown_fields_mode`,
  `off_schema_refusal`) ;
- **sous un composite DÉCLARÉ** (`object.fields`, `list.of.fields`), le référentiel
  est fermé : l'attribut que la déclaration ne nomme pas est refusé en nommant
  l'élément fautif (`_unknown_subkeys`, `_off_schema`, `_unknown_subkey_refusal`).

Y vit aussi `couche_mal_ecrite` : `champ.comentaire` n'est pas une clé inconnue de
plus, c'est une couche mal orthographiée — le dire change le geste de correction. Et
`types_geles_warning`, qui parle d'une ligne DÉJÀ hors format sans rien refuser :
l'écriture est passée, le fait est dit à celui qui traverse.

Ce qu'il ne tient pas :
- **les attributs de SCHÉMA non reconnus** (`zorglub: true` sur une colonne) →
  `cles_inconnues.py` et `vocabulaire.py` — ici on juge une LIGNE, pas un format ;
- **le refus d'une valeur mal typée** sur une clé bien déclarée → `validation.py` ;
- **le vocabulaire des couches** que `couche_mal_ecrite` compare → `couches.py`.
"""
from __future__ import annotations

from typing import Optional

from .couches import LAYER_KEYS, split_layer
from .declaration import _fields
from .phrases_de_refus import cle_la_plus_proche

def off_schema_keys(schema: Optional[dict], data: dict) -> list[str]:
    """Clés de la row ÉCRITE qu'aucun field du schéma ne déclare (chemins pointés
    pour les sous-records : `contacts[].email_pro`) — le signal de l'issue #294.

    Un nom hors schéma n'est PAS refusé et n'est pas perdu : il crée une colonne
    libre et la valeur persiste (contrat 0016, « tout autre champ s'affiche, il ne
    débloque rien »). Mais cette colonne est **hors du format** — l'interface et
    les consommateurs du schéma ne la lisent pas. Sur un renommage de champs (cas
    ordinaire : le format évolue, les agents ne sont pas tous relancés ensemble),
    le travail atterrit dans une colonne que personne ne regarde, et rien ne le
    signale : un agent écrit, reçoit un accusé de réception, passe à la ligne.
    D'où ce relevé, rendu à l'appelant qui peut le vérifier.

    Vide hors mode `strict` : un champ libre y est un droit explicite du contrat
    (c'est ce qui permet d'explorer un tableau avant de le typer), pas une anomalie.
    Vide aussi si le schéma strict ne déclare AUCUN field — sans référentiel, tout
    serait « hors schéma », ce qui n'informe personne.

    ⚠️ **Ce relevé lit la row ÉCRITE, jamais la row SERVIE — et un contrôle bâti sur
    la seconde surcompte.** Une couche se pose imbriquée (`{valeur, comment}`), se
    stocke imbriquée, et n'est aplatie qu'à la LECTURE par `flat_layers` : le
    consommateur voit `site_web.comment` comme une clé de premier niveau, alors
    qu'aucune colonne de ce nom n'existe. Un instrument qui compare les clés servies
    aux `fields` déclarés compte donc chaque couche comme une colonne inventée. *Le
    31/08/2026, ce piège a valu à une campagne un relevé de 304 colonnes « inventées »
    qui n'existaient pas, et une enquête pour « silence » du rapporteur qui, lui,
    n'avait rien manqué.* Comparer aux `fields` ne vaut que sur la forme posée ; sur
    la forme servie, il faut d'abord écarter `<colonne déclarée>.<couche>`."""
    if not isinstance(schema, dict) or not schema.get("strict"):
        return []
    fields = _fields(schema)
    if not fields or not isinstance(data, dict):
        return []
    return sorted(_off_schema(fields, data, ""))


def _unknown_subkeys(fields: list, data: dict) -> list[str]:
    """Les clés de `data` qu'aucun field de `fields` ne couvre — le prédicat UNIQUE
    du « hors du référentiel », partagé par le SIGNAL (`_off_schema`, #294) et par le
    REFUS d'un composite déclaré (`_row_errors`, #544). Deux implémentations
    donneraient deux définitions de la même chose, et c'est l'appelant qui paierait
    la différence — le défaut déjà payé sur `split_layer` (juste sur un verbe, faux
    sur trois).

    Deux règles, et elles valent à tous les étages :

    - **sans référentiel, rien n'est hors référentiel** : un composite qui ne déclare
      AUCUN field ne ferme rien (tout y serait « inconnu », ce qui n'informe
      personne) — c'est exactement le contrat d'une liste libre, et la même règle
      qu'au premier niveau (`off_schema_keys` sur un strict sans field) ;
    - **une COUCHE n'est pas un attribut** : la forme servie d'un item aplatit ses
      couches (`email.origine`, oto#22 §2), donc un aller-retour lecture → écriture
      les repose telles quelles. Les refuser casserait le geste le plus ordinaire.
    """
    declared = {f["key"] for f in fields
                if isinstance(f, dict) and isinstance(f.get("key"), str) and f["key"]}
    if not declared or not isinstance(data, dict):
        return []
    out = []
    for key in data:
        if key in declared:
            continue
        base, layer = split_layer(key)
        if layer and base in declared:
            continue
        out.append(key)
    return sorted(out)


def _unknown_subkey_refusal(path: str, fields: list) -> str:
    """Le refus d'un attribut non déclaré DANS un composite (#544).

    Il dit les trois choses qu'un agent doit savoir pour ne pas réessayer à
    l'identique : où (le chemin, RANG compris), ce qui était attendu (les attributs
    déclarés, dans leur ordre), et pourquoi ce n'est pas la même chose qu'au premier
    niveau — là, une clé inconnue crée une colonne libre que l'interface affiche ;
    ici, elle n'a nulle part où exister."""
    noms = [f["key"] for f in fields
            if isinstance(f, dict) and isinstance(f.get("key"), str) and f["key"]]
    dispo = ", ".join(f"`{n}`" for n in noms)
    # oto#135 : la clé déclarée la plus proche, quand il y en a une — `emial` a une
    # destination, et le refus la dit au lieu de faire relire la liste.
    proche = cle_la_plus_proche(path.rpartition(".")[2], noms)
    dispo += f" (le plus proche : `{proche}`)" if proche else ""
    return (f"{path}: attribut non déclaré — le tableau est en format `strict` et ce "
            f"sous-record ferme ses attributs : {dispo}. Rien n'a été écrit. "
            "Contrairement à une colonne de premier niveau, un attribut inconnu ne "
            "crée PAS de colonne libre : il serait stocké là où ni le schéma, ni "
            "l'interface, ni l'export à plat ne le lisent. Écris-le sous un nom "
            "déclaré, ou déclare l'attribut (`data_patch_schema`) puis réécris.")


def _off_schema(fields: list, data: dict, prefix: str) -> set:
    """Clés de `data` absentes de `fields`, en descendant dans les composites
    DÉCLARÉS (un champ déjà hors schéma n'est pas exploré : on ne sait pas ce
    qu'il devrait contenir). Les items d'une liste sont agrégés sur un chemin
    unique `clé[].sous_clé` — un lot de 300 contacts ne rend pas 300 lignes.

    ⚠️ Depuis #544, les chemins IMBRIQUÉS ne sortent plus d'ici sur un tableau
    `strict` : le refus arrive avant le relevé (`_check_row` lève, puis relève).
    La descente reste le contrat de cette fonction — elle décrit ce qui est hors du
    format, indépendamment de qui refuse — et elle couvre encore le cas où la
    déclaration n'a pas de référentiel."""
    declared = {f["key"]: f for f in fields
                if isinstance(f.get("key"), str) and f["key"]}
    out: set = {f"{prefix}{k}" for k in _unknown_subkeys(fields, data)}
    for key, value in data.items():
        f = declared.get(key)
        if f is None:
            continue
        ftype, sub = f.get("type"), None
        if ftype == "object" and isinstance(value, dict):
            sub = f.get("fields")
            if isinstance(sub, list):
                out |= _off_schema([x for x in sub if isinstance(x, dict)],
                                   value, f"{prefix}{key}.")
        elif ftype == "list" and isinstance(value, list):
            of = f.get("of")
            sub = of.get("fields") if isinstance(of, dict) else None
            if isinstance(sub, list):
                declared_sub = [x for x in sub if isinstance(x, dict)]
                for item in value:
                    if isinstance(item, dict):
                        out |= _off_schema(declared_sub, item, f"{prefix}{key}[].")
    return out


def off_schema_warning(keys: list) -> Optional[str]:
    """La phrase actionnable qui accompagne `off_schema_keys` — une liste nue ne
    dit pas ce qu'elle implique. None si rien n'est hors schéma."""
    if not keys:
        return None
    noms = ", ".join(f"`{k}`" for k in keys)
    return (f"écrit HORS SCHÉMA : {noms} — le tableau déclare un format strict, ces "
            "colonnes en sortent : elles sont stockées et lisibles, mais l'interface "
            "et tout ce qui s'appuie sur le schéma les ignorent. Si c'est une faute de "
            "nom (champ renommé depuis), relis le format avec `data_get_schema` et "
            "réécris sous le bon nom ; si le champ est voulu, déclare-le au schéma.")


def types_geles_warning(gelees: list) -> Optional[str]:
    """La phrase qui accompagne le relevé des types déjà hors format.

    ⚠️ **Ce n'est pas un reproche à l'appelant** : il n'a pas écrit ces colonnes, et
    son écriture a réussi. C'est un fait sur la LIGNE, dit à celui qui est en train
    de la toucher — le seul qui passera par là. Le refuser aurait gelé la ligne pour
    toujours ; le taire l'aurait laissée pourrir sans témoin."""
    if not gelees:
        return None
    noms = ", ".join(f"`{g['champ']}`" for g in gelees)
    return (f"ton écriture est passée. Signalement sur une AUTRE partie de la ligne : "
            f"{noms} — la valeur déjà en base n'y respecte plus le format déclaré. Ce "
            "n'est pas ton geste et rien n'a été refusé : ces colonnes ont été écrites "
            "avant que le format ne soit posé, ou le format a changé depuis. Tant "
            "qu'elles restent ainsi, elles s'affichent et se lisent, mais toute "
            "déclaration qui s'appuie dessus les ignore. Pour les remettre en règle, "
            "écris-y une valeur conforme — le refus détaillé te dira laquelle.")


# ── le TROISIÈME état de `strict` au premier niveau (#614/#678) ──────────────
#
# `strict` porte deux contrats sous un seul mot : rapporteur au premier niveau
# (`hors_schema`, arbitrage #294), refus dans un sous-record déclaré (#544). Le
# nom promettait le second et livrait le premier ; on a cessé de surveiller ce
# qu'on croyait gardé, et douze clés inventées sont entrées en vingt-deux
# occurrences, dont trois dans des fiches clientes.
#
# **L'asymétrie ne se ferme pas, elle se PARAMÈTRE.** Au premier niveau, un nom
# inconnu crée une vraie colonne qu'on peut déclarer après coup : c'est ce qui
# permet d'explorer un tableau avant de le typer, et c'est un droit du contrat
# 0016. Le défaut reste donc `report`. Ce qu'on ajoute est un troisième état,
# opt-in table par table, pour le tableau qui a FINI d'être exploré.
#
# ⚠️ Clé distincte plutôt que `strict: "refuse"` : `strict` est lu comme un
# booléen à cinq endroits (`validation_active`, `off_schema_keys`, `validate_row`,
# `claimable.erreurs`, `_orphan_columns_warning`) — en changer le type ferait
# mentir chaque lecture existante, en silence, et sur le chemin chaud.
UNKNOWN_FIELDS_MODES = ("report", "reject")

# Ce que le refus cite du référentiel avant d'abréger. Un tableau à soixante
# colonnes rendrait un mur que personne ne lit — et le budget d'un retour d'outil
# est le budget de tout le monde (`docs/conventions.md`).
_REFERENTIEL_CITE = 15


def unknown_fields_mode(schema: Optional[dict]) -> str:
    """`report` (le défaut, comportement de #294) ou `reject` (#614/#678).

    Une valeur illisible rend `report` : elle est refusée à la POSE
    (`validate_schema_def`), donc elle ne peut venir que d'une écriture hors
    surface — et dans le doute on ne DURCIT pas un tableau sur une déclaration
    qu'on ne comprend pas. C'est le sens sûr : l'autre fermerait un tableau
    vivant sur une faute de frappe."""
    if not isinstance(schema, dict):
        return "report"
    mode = schema.get("unknown_fields")
    return mode if mode in UNKNOWN_FIELDS_MODES else "report"


def couche_mal_ecrite(cle: str, declarees) -> Optional[tuple]:
    """`effectif_comment` → `("effectif", "comment")` — ou `None` (oto#63).

    ⚠️ **La seule suggestion qu'on s'autorise, et elle n'est pas une devinette.** La
    règle de `off_schema_refusal` reste entière : on ne pointe jamais la colonne
    « la plus proche », parce qu'une destination inventée envoie la valeur dans une
    colonne juste — pire qu'un refus sec (#678). Ici on ne cherche rien de proche : on
    RECONNAÎT une décomposition exacte. La clé refusée s'écrit `<colonne>_<couche>`,
    la couche est l'un des trois noms que le serveur connaît, et la colonne est
    DÉCLARÉE au schéma. Les trois conditions, sinon rien.

    Le geste qu'on répare : l'agent lit sa ligne réservée à plat, y voit
    `effectif.comment`, veut écrire un commentaire — et produit `effectif_comment`,
    parce qu'un point n'a pas l'air d'un nom de champ et que rien ne lui dit qu'il est
    adressable. Cinq colonnes fantômes sur une mission réelle sont exactement ça, et
    **toutes sont des `comment`**, jamais des origines ni des liens.

    ⚠️ **Sur un tableau qui refuse les colonnes inconnues, cette faute ne crée plus une
    colonne invisible : elle fait perdre la fiche entière.** Le durcissement a
    transformé une perte silencieuse en perte totale sans que le défaut bouge — plus on
    durcit, plus il coûte, et c'est ce qui rend ce message urgent.

    ⚠️ Ambiguïté écartée par construction : un nom de colonne ne peut pas se terminer
    par `_comment` ET être déclaré, sans que ce soit précisément la colonne qu'on
    cherche. Si `effectif_comment` était elle-même déclarée, la clé ne serait pas
    refusée et on ne passerait pas ici.
    """
    for couche in LAYER_KEYS:
        suffixe = f"_{couche}"
        if cle.endswith(suffixe) and len(cle) > len(suffixe):
            colonne = cle[:-len(suffixe)]
            if colonne in declarees:
                return colonne, couche
    return None


def enveloppe_probable(schema: Optional[dict], data: dict) -> bool:
    """La ligne est-elle ENVELOPPÉE plutôt qu'écrite ? — aucune clé ne correspond.

    Sur les routes d'écriture, **le corps EST la ligne**. Qui suit la convention
    habituelle l'enveloppe dans un objet qui la nomme (`{"row": {…}}`) et fabrique une
    colonne réellement appelée `row`, contenant toute la ligne. La réponse est alors
    indiscernable d'une écriture réussie.

    ⚠️ **Le critère est l'ABSENCE TOTALE de correspondance**, pas la présence d'une
    clé inconnue : ajouter une colonne libre à un tableau schématisé reste un droit du
    contrat (0016), et le refuser durcirait un contrat servi. Ce qui n'a aucun sens,
    c'est une ligne qui ne touche **pas une seule** des colonnes déclarées.

    ⚠️ **Mesuré avant d'être posé, sur la base servie** : 67 980 lignes de tableaux à
    colonnes déclarées, **une seule** ne correspondait à rien — sur un tableau qui en
    compte 1 986 et déclare huit colonnes. Le critère ne décrit donc aucun régime
    normal : il ne nomme que l'accident.

    Rend `False` dès qu'il manque de quoi juger — schéma sans colonnes déclarées
    (tableau libre), ligne vide, données non exploitables : on n'invente pas un refus
    sur une absence d'information.
    """
    if not isinstance(data, dict) or not data:
        return False
    declarees = {f["key"] for f in _fields(schema)
                 if isinstance(f.get("key"), str) and f["key"]}
    if not declarees:
        return False                      # tableau libre : rien à quoi comparer
    posees = {k for k in data if isinstance(k, str) and not k.startswith("_")}
    return bool(posees) and not (posees & declarees)


def off_schema_refusal(schema: Optional[dict],
                       data: dict) -> tuple[list[str], dict]:
    """Le refus des colonnes non déclarées au PREMIER niveau → `(messages, details)`.

    Même prédicat que le relevé (`off_schema_keys`, donc `_unknown_subkeys`) : le
    rapporteur et le refuseur ne peuvent pas diverger sur ce qu'est « hors du
    référentiel ». Deux définitions de la même chose, et c'est l'appelant qui
    paierait la différence.

    ⚠️ **Aucune destination n'est DEVINÉE, et c'est le point.** Une colonne non
    déclarée n'a, par construction, aucune destination : pointer la colonne « la
    plus proche » enverrait la valeur dans une colonne juste, ce qui est pire
    qu'un refus sec — *une destination inventée est pire qu'une destination
    absente* (#678). `details` reste donc vide, et le message DIT qu'aucune
    colonne ne porte ce nom, au lieu de laisser deviner.

    ⚠️ **Une seule exception, et elle ne devine rien** (oto#63) : quand la clé
    refusée se décompose EXACTEMENT en `<colonne déclarée>_<couche connue>`, ce
    n'est pas une colonne inconnue — c'est une couche dont le nom a été écrit avec
    un souligné au lieu d'un point. Là, la destination n'est pas rapprochée, elle
    est lue dans la clé elle-même ; se taire enverrait l'appelant déclarer une
    colonne qui ne devrait pas exister. Cf. `couche_mal_ecrite`."""
    if unknown_fields_mode(schema) != "reject":
        return [], {}
    keys = off_schema_keys(schema, data)
    if not keys:
        return [], {}
    dispo = [f["key"] for f in _fields(schema)
             if isinstance(f.get("key"), str) and f["key"]]
    cite = ", ".join(f"`{k}`" for k in dispo[:_REFERENTIEL_CITE])
    if len(dispo) > _REFERENTIEL_CITE:
        cite += f" (+{len(dispo) - _REFERENTIEL_CITE} autres, `data_get_schema`)"
    noms = ", ".join(f"`{k}`" for k in keys)
    # oto#63 : parmi les clés refusées, celles qui sont une COUCHE mal écrite —
    # `effectif_comment` au lieu de `effectif.comment`. Reconnaissance exacte, pas
    # rapprochement : cf. `couche_mal_ecrite`.
    couches = [(k, *c) for k in keys if (c := couche_mal_ecrite(k, set(dispo)))]
    # ⚠️ PAS de branche pour la clé POINTÉE dont la colonne est absente, et c'est
    # mesuré : `_refuse_dotted_names` (datastore/points.py) lève avant, sur les CINQ
    # chemins d'écriture, avec un message meilleur que celui qu'on écrirait ici — il
    # dit où il a cherché (« ni dans cette écriture, ni sur la ligne visée, ni au
    # schéma ») et donne la forme imbriquée. Ajouter la nôtre aurait été du code que
    # rien n'atteint, et qui aurait fait croire à un second chemin.
    if couches:
        quoi = " ; ".join(
            f"`{k}` → `{col}.{couche}`" for k, col, couche in couches)
        details = {"expected_column": f"{couches[0][1]}.{couches[0][2]}"}
        return ([f"{noms} : rien n'a été écrit. ⚠️ Ce n'est pas une colonne "
                 f"inconnue, c'est une COUCHE dont le nom s'écrit avec un POINT — "
                 f"{quoi}. La colonne existe, c'est la façon de l'adresser qui "
                 f"diffère : `{{\"{couches[0][1]}\": {{\"{couches[0][2]}\": …}}}}` "
                 f"écrit la même chose, et c'est la forme que "
                 f"`layers=\"nested\"` te rend à la lecture."], details)
    return ([f"{noms} : aucune colonne déclarée ne porte ce nom, et ce tableau "
             f"refuse les colonnes non déclarées (`unknown_fields: \"reject\"`) — "
             f"rien n'a été écrit. Colonnes du tableau : {cite}. Écris sous un nom "
             f"déclaré, ou déclare la colonne (`data_patch_schema`) puis réécris. "
             f"⚠️ Ne réessaie pas sous une variante du même nom : elle sera "
             f"refusée pareil, et la valeur n'a nulle part où aller ici."], {})
