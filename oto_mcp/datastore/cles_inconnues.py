"""Le REFUS des clés de schéma qu'aucun niveau n'admet (01/10/2026 — oto#34, #35, #127).

Le validateur acceptait n'importe quelle clé, puis il l'a SIGNALÉE (oto#56) : un
avertissement, jamais un refus. Il n'a pas suffi. Mesuré le 01/10 sur 442 tableaux à
schéma : 45 portaient des clés que personne ne lisait — `labels` sur une colonne,
`enum` pour `options`, `note`, `editable`, `depends_on`… Un avertissement sur un appel
qui réussit ne se lit pas ; et qui écrit `read_only` pour `readonly` croit sa colonne
verrouillée.

## Ce qui est refusé, et ce qui ne l'est pas

Toute clé qu'un niveau n'admet pas — tête, colonne, sous-champ, élément de liste
(`of`), bloc `lifecycle` — est refusée, à la pose ET au patch, sur les deux faces :
`validate_schema_def` appelle `refus`, et `set_schema` (par où repasse le patch) appelle
`validate_schema_def`. Les listes admises sont DÉCLARÉES par niveau
(`schema_keys.ADMISES`) ; la dérivation des `.get()` du code n'en est que la garde de
banc, jamais le fondement.

⚠️ **Le refus porte sur ce que le GESTE pose ou modifie, jamais sur ce qui est déjà
stocké.** Une clé inconnue présente dans l'ancien schéma, au même endroit et avec la
même valeur, passe : sans quoi le patch d'une AUTRE colonne — qui repasse le schéma
fusionné par `set_schema` — serait refusé sur un tableau que personne n'a encore
migré, et le tableau deviendrait inmodifiable du jour au lendemain. Même parti que
`required_layers: []` (`definition.py`) : un geste qui marchait ne cesse pas de marcher
sur un schéma que personne n'a touché. Une clé stockée qu'on MODIFIE, elle, est
refusée — c'est un geste. Ce qui reste stocké est dit en `warning` (`residus_warning`),
à la pose comme à la lecture, jusqu'à ce que `scripts/durcir_schemas.py` l'ait rangé.

## La zone libre

`meta` — un objet, admis à chaque niveau, transporté tel quel, jamais lu, borné à
`schema_keys.META_MAX_OCTETS` octets en JSON. C'est là qu'un consommateur range ses
propres déclarations : le vocabulaire est fermé, `meta` est la porte.

## Le refus dit où aller

Une clé inconnue n'est presque jamais une invention gratuite : c'est une faute de frappe
(`read_only`), une clé d'un AUTRE niveau (`states` sur une colonne), un paramètre
d'appel rangé dans le schéma (`semantic_search`), un texte d'aide sous un autre nom
(`note`), ou une clé retirée (`origine`). Chaque cas a sa phrase, parce qu'ils ne se
corrigent pas pareil — « clé inconnue » fait chercher une faute de frappe sur un mot
juste.
"""
from __future__ import annotations

from typing import Any, Iterator, Optional

from . import reglages
from . import schema_keys as sk
from .phrases_de_refus import cle_la_plus_proche

#: Un pas dans le schéma : `("fields", <clé>)`, `("of",)` ou `("lifecycle",)`.
Pas = tuple


def parcours(schema: Any) -> Iterator[tuple[str, tuple, dict]]:
    """`(niveau, chemin, nœud)` pour chaque nœud OBJET du schéma, aux cinq niveaux.

    On ne descend que par une clé ADMISE au niveau où l'on est : un `lifecycle` posé
    sur un sous-champ est lui-même la clé refusée, son contenu n'a pas de niveau."""
    if not isinstance(schema, dict):
        return
    yield "tete", (), schema
    yield from _descendre(schema, "tete", ())


def _descendre(noeud: dict, niveau: str, chemin: tuple) -> Iterator:
    admises = sk.ADMISES[niveau]
    enfant = "champ" if niveau == "tete" else "sous_champ"
    if "fields" in admises and isinstance(noeud.get("fields"), list):
        for f in noeud["fields"]:
            if isinstance(f, dict):
                pas = chemin + (("fields", f.get("key")),)
                yield enfant, pas, f
                yield from _descendre(f, enfant, pas)
    if niveau == "tete":
        return
    if "of" in admises and isinstance(noeud.get("of"), dict):
        pas = chemin + (("of",),)
        yield "element", pas, noeud["of"]
        yield from _descendre(noeud["of"], "element", pas)
    if "lifecycle" in admises and isinstance(noeud.get("lifecycle"), dict):
        yield "cycle", chemin + (("lifecycle",),), noeud["lifecycle"]


def suivre(schema: Any, chemin: tuple) -> Optional[dict]:
    """Le nœud au même `chemin` dans un autre schéma (l'ancien), ou `None`. Une colonne
    se retrouve par sa CLÉ, jamais par sa position : un patch peut réordonner."""
    noeud = schema if isinstance(schema, dict) else None
    for pas in chemin:
        if noeud is None:
            return None
        if pas[0] == "fields":
            noeud = next((f for f in (noeud.get("fields") or [])
                          if isinstance(f, dict) and f.get("key") == pas[1]), None)
        else:
            v = noeud.get(pas[0])
            noeud = v if isinstance(v, dict) else None
    return noeud


def nom_du_chemin(chemin: tuple) -> str:
    """`fields.contacts.of.fields.email` — la forme JSON du schéma, lisible telle quelle."""
    if not chemin:
        return "tête"
    return ".".join(f"fields.{p[1] if p[1] is not None else '?'}"
                    if p[0] == "fields" else p[0] for p in chemin)


def inconnues(niveau: str, noeud: dict) -> list[str]:
    """Les clés de `noeud` que `niveau` n'admet pas, triées."""
    return sorted(str(k) for k in noeud if k not in sk.ADMISES[niveau])


def _deja_stockee(ancien_noeud: Optional[dict], cle: str, valeur: Any) -> bool:
    return isinstance(ancien_noeud, dict) and cle in ancien_noeud \
        and ancien_noeud[cle] == valeur


def erreurs_meta(chemin: tuple, valeur: Any) -> list[str]:
    lieu = nom_du_chemin(chemin)
    if not isinstance(valeur, dict):
        return [f"{lieu}.meta doit être un objet {{…}} — reçu {type(valeur).__name__}. "
                f"C'est la zone libre du schéma : des clés à toi, transportées telles "
                f"quelles, jamais lues"]
    taille = sk.taille_json(valeur)
    if taille > sk.META_MAX_OCTETS:
        return [f"{lieu}.meta fait {taille} octets en JSON, au plus "
                f"{sk.META_MAX_OCTETS} — c'est une annotation du format, relue à chaque "
                f"écriture de ligne ; ce qui est volumineux va dans les données ou dans "
                f"un document"]
    return []


def refus(schema: Any, ancien: Any = None) -> list[str]:
    """Les phrases de refus des clés que CE GESTE pose ou modifie sans que leur niveau
    les admette, et des `meta` mal formés. Vide = rien à refuser.

    `ancien` = le schéma en place : une clé qui y figure au même chemin avec la même
    valeur n'est pas posée par le geste, elle passe (cf. l'en-tête du module)."""
    out: list[str] = []
    for niveau, chemin, noeud in parcours(schema):
        avant = suivre(ancien, chemin)
        poses = [c for c in inconnues(niveau, noeud)
                 if not _deja_stockee(avant, c, noeud[c])]
        # oto#127 : les trois anciens réglages de tête se refusent ENSEMBLE — leur
        # équivalent se calcule sur la combinaison, jamais clé par clé.
        anciens = [c for c in poses if niveau == "tete" and c in reglages.ANCIENS]
        if anciens:
            out.append(reglages.refus_anciens(noeud, anciens))
        for cle in poses:
            if cle not in anciens:
                out.append(phrase(niveau, chemin, cle, noeud[cle]))
        if sk.META in noeud and not _deja_stockee(avant, sk.META, noeud[sk.META]):
            out.extend(erreurs_meta(chemin, noeud[sk.META]))
    return out


def _autres_niveaux(niveau: str, cle: str) -> list[str]:
    return [n for n in sk.NIVEAUX if n != niveau and cle in sk.ADMISES[n]]


def phrase(niveau: str, chemin: tuple, cle: str, valeur: Any = None) -> str:
    """Le refus d'UNE clé : où, laquelle, et où elle va."""
    admises = sk.ADMISES[niveau]
    lieu = nom_du_chemin(chemin)
    debut = f"{lieu} : `{cle}` n'est pas admise {sk.NOMS_DE_NIVEAU[niveau]}"
    if cle in sk.TEXTES_D_AIDE and "description" in admises:
        return (f"{debut} — un texte d'aide s'écrit dans `description`, le seul : "
                f"{', '.join('`' + t + '`' for t in sk.TEXTES_D_AIDE)} y ont été "
                f"repliés le 01/10/2026")
    if cle in sk.CLES_RETIREES:
        return f"{debut} — {sk.CLES_RETIREES[cle]}"
    if cle in sk.PARAMETRES_HORS_SCHEMA:
        return (f"{debut} — c'est un PARAMÈTRE de l'appel (`data_set_schema("
                f"{cle}=…)`), pas une clé de schéma : posé ici il serait stocké et sans "
                f"effet. Passe-le à côté de `schema`")
    ailleurs = _autres_niveaux(niveau, cle)
    if niveau == "element" and cle == "max_items":
        col = chemin[-2][1] if len(chemin) >= 2 and chemin[-2][0] == "fields" else "?"
        return (f"{debut} — `max_items` borne le nombre d'éléments de la LISTE, pas un "
                f"élément : posé dans `of`, personne ne le lit. Pose-le sur la colonne, "
                f"à côté de son type : {{\"key\": \"{col}\", \"type\": \"list\", "
                f"\"max_items\": {valeur!r}, \"of\": {{...}}}}")
    if "cycle" in ailleurs:
        return (f"{debut} — elle se pose DANS le bloc `lifecycle` de la colonne "
                f"(`lifecycle.{cle}`) ; si elle ne décrit pas le cycle de vie, c'est "
                f"une annotation : range-la dans `meta`")
    if "tete" in ailleurs and niveau != "tete":
        return f"{debut} — c'est un réglage de TÊTE du schéma, à côté de `fields`"
    if niveau == "tete" and "champ" in ailleurs:
        return (f"{debut} — c'est un attribut de COLONNE : pose-le dans le `fields` de "
                f"la colonne qu'il vise")
    if cle in sk.PREMIER_NIVEAU_SEULEMENT and niveau in ("sous_champ", "element"):
        return (f"{debut} — `{cle}` ne se pose qu'au premier niveau, sur une colonne : "
                f"sous un sous-record rien ne le lit, et une déclaration que rien ne lit "
                f"n'est pas inerte, elle ment")
    if niveau == "element" and "sous_champ" in ailleurs:
        return (f"{debut} — l'élément d'une liste n'en lit que le type, les options et "
                f"les sous-champs : pose-la sur un sous-champ (`of.fields`)")
    proche = sk.FAUTES_CONNUES.get(cle)
    if proche not in admises:
        proche = cle_la_plus_proche(cle, sorted(admises - {sk.META}))
    if proche:
        return f"{debut} — voulais-tu `{proche}` ?"
    return (f"{debut} — les clés admises ici sont "
            f"{', '.join('`' + c + '`' for c in sorted(admises))} ; une annotation à "
            f"toi va dans `meta` (transportée telle quelle, jamais lue)")


def residus_warning(schema: Any) -> Optional[str]:
    """Ce que le schéma porte ENCORE d'inconnu — stocké avant la fermeture du
    vocabulaire, donc toléré tant qu'on n'y touche pas. Dit au lecteur comme à
    l'auteur : le lecteur doit savoir laquelle de deux clés fait foi (`options`, pas
    l'`enum` d'à côté), l'auteur que la modifier sera refusé."""
    trouvees, font_foi = [], set()
    # oto#124 : `unknown_columns` stocké a sa propre phrase — il est encore LU jusqu'au
    # 21/10/2026 et la plateforme le retire elle-même (`scripts/retirer_unknown_columns`).
    retire = (isinstance(schema, dict) and reglages.UNKNOWN_COLUMNS in schema)
    for niveau, chemin, noeud in parcours(schema):
        for cle in inconnues(niveau, noeud):
            if retire and not chemin and cle == reglages.UNKNOWN_COLUMNS:
                continue
            trouvees.append(f"`{nom_du_chemin(chemin)}.{cle}`" if chemin
                            else f"`{cle}` (tête)")
            # Ce qui fait foi : la cousine admise, quand elle est posée À CÔTÉ.
            if sk.FAUTES_CONNUES.get(cle) in noeud:
                font_foi.add(sk.FAUTES_CONNUES[cle])
    if not trouvees:
        return (reglages.residu_unknown_columns(schema[reglages.UNKNOWN_COLUMNS]) + "."
                if retire else None)
    anciens = [k for k in reglages.ANCIENS if isinstance(schema, dict) and k in schema]
    msg = (f"ce schéma porte des clés qu'aucun niveau n'admet, posées avant la "
           f"fermeture du vocabulaire (01/10/2026) : {', '.join(trouvees[:8])}"
           + (", …" if len(trouvees) > 8 else "")
           + ". Oto ne les lit pas ; elles restent stockées tant qu'on n'y touche pas, "
             "et les modifier est refusé. Un texte d'aide va dans `description`, une "
             "annotation dans `meta`, le reste se retire.")
    if font_foi:
        msg += (" Ce qui fait foi : " + ", ".join(f"`{k}`" for k in sorted(font_foi))
                + " — la clé inconnue d'à côté est un résidu, quoi qu'elle dise.")
    if anciens:
        msg += f" Pour {', '.join(f'`{k}`' for k in anciens)} : {reglages.residu(schema)}."
    if retire:
        msg += " " + reglages.residu_unknown_columns(schema[reglages.UNKNOWN_COLUMNS]) + "."
    return msg
