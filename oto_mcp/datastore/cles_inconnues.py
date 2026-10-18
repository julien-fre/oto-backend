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
        return "top level"
    return ".".join(f"fields.{p[1] if p[1] is not None else '?'}"
                    if p[0] == "fields" else p[0] for p in chemin)


def inconnues(niveau: str, noeud: dict) -> list[str]:
    """Les clés de `noeud` que `niveau` n'admet pas, triées."""
    return sorted(str(k) for k in noeud if k not in sk.ADMISES[niveau])


def _deja_stockee(ancien_noeud: Optional[dict], cle: str, valeur: Any) -> bool:
    return isinstance(ancien_noeud, dict) and cle in ancien_noeud \
        and ancien_noeud[cle] == valeur


def erreurs_meta(chemin: tuple, valeur: Any, niveau: Optional[str] = None,
                 ancien: Any = None) -> list[str]:
    """`ancien` = le `meta` en place au même chemin : une clé d'ombre qui y figure avec
    la même valeur n'est pas posée par le geste, elle passe (même parti que `refus`)."""
    lieu = nom_du_chemin(chemin)
    if not isinstance(valeur, dict):
        return [f"{lieu}.meta must be an object {{…}} — got {type(valeur).__name__}. "
                f"It is the schema's free zone: keys of your own, carried as is, "
                f"never read"]
    taille = sk.taille_json(valeur)
    if taille > sk.META_MAX_OCTETS:
        return [f"{lieu}.meta is {taille} bytes in JSON, at most "
                f"{sk.META_MAX_OCTETS} — it is an annotation of the format, reread at every "
                f"row write; anything bulky goes in the data or in "
                f"a document"]
    # ⚠️ Le cas du 14/10/2026 : `meta: {"options": [...]}` sur trois
    # colonnes `enum`. Accepté sans un mot, puisque `meta` n'est pas lu — et c'est
    # bien le problème : la liste ne contraignait rien, le Kanban ne la voyait pas,
    # et l'auteur la croyait déclarée. Une clé que CE niveau admet n'a rien à faire
    # dans `meta` : rangée là, elle a l'air posée et ne l'est pas. Refusée, avec le
    # renvoi au bon endroit.
    if niveau is None:
        return []
    ombres = [c for c in sorted(set(valeur) & (sk.ADMISES[niveau] - {sk.META}))
              if not _deja_stockee(ancien, c, valeur[c])]
    return [f"{lieu}.meta.{c}: `{c}` is a key allowed {sk.NOMS_DE_NIVEAU[niveau]} — "
            f"stored in `meta`, it is kept and has NO EFFECT (nobody reads "
            f"`meta`). Set it next to `meta`, not inside it"
            for c in ombres]


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
            out.extend(erreurs_meta(chemin, noeud[sk.META], niveau,
                                    avant.get(sk.META) if isinstance(avant, dict)
                                    else None))
    return out


def _autres_niveaux(niveau: str, cle: str) -> list[str]:
    return [n for n in sk.NIVEAUX if n != niveau and cle in sk.ADMISES[n]]


def phrase(niveau: str, chemin: tuple, cle: str, valeur: Any = None) -> str:
    """Le refus d'UNE clé : où, laquelle, et où elle va."""
    admises = sk.ADMISES[niveau]
    lieu = nom_du_chemin(chemin)
    debut = f"{lieu}: `{cle}` is not allowed {sk.NOMS_DE_NIVEAU[niveau]}"
    if cle in sk.TEXTES_D_AIDE and "description" in admises:
        return (f"{debut} — a help text goes in `description`, the only one: "
                f"{', '.join('`' + t + '`' for t in sk.TEXTES_D_AIDE)} were "
                f"folded into it on 2026-10-01")
    if cle in sk.CLES_RETIREES:
        return f"{debut} — {sk.CLES_RETIREES[cle]}"
    if cle in sk.PARAMETRES_HORS_SCHEMA:
        return (f"{debut} — it is a PARAMETER of the call (`data_set_schema("
                f"{cle}=…)`), not a schema key: set here it would be stored and have no "
                f"effect. Pass it next to `schema`")
    ailleurs = _autres_niveaux(niveau, cle)
    if niveau == "element" and cle == "max_items":
        col = chemin[-2][1] if len(chemin) >= 2 and chemin[-2][0] == "fields" else "?"
        return (f"{debut} — `max_items` bounds the number of elements of the LIST, not an "
                f"element: set in `of`, nobody reads it. Set it on the column, "
                f"next to its type: {{\"key\": \"{col}\", \"type\": \"list\", "
                f"\"max_items\": {valeur!r}, \"of\": {{...}}}}")
    if "cycle" in ailleurs:
        return (f"{debut} — it goes INSIDE the column's `lifecycle` block "
                f"(`lifecycle.{cle}`); if it does not describe the lifecycle, it is "
                f"an annotation: put it in `meta`")
    if "tete" in ailleurs and niveau != "tete":
        return f"{debut} — it is a TOP-LEVEL setting of the schema, next to `fields`"
    if niveau == "tete" and "champ" in ailleurs:
        return (f"{debut} — it is a COLUMN attribute: set it in the `fields` entry of "
                f"the column it targets")
    if cle in sk.PREMIER_NIVEAU_SEULEMENT and niveau in ("sous_champ", "element"):
        return (f"{debut} — `{cle}` is set only at the first level, on a column: "
                f"under a sub-record nothing reads it, and a declaration that nothing reads "
                f"is not inert, it lies")
    if niveau == "element" and "sous_champ" in ailleurs:
        return (f"{debut} — a list element reads only its type, its options and "
                f"its sub-fields: set it on a sub-field (`of.fields`)")
    proche = sk.FAUTES_CONNUES.get(cle)
    if proche not in admises:
        proche = cle_la_plus_proche(cle, sorted(admises - {sk.META}))
    if proche:
        return f"{debut} — did you mean `{proche}`?"
    return (f"{debut} — the keys allowed here are "
            f"{', '.join('`' + c + '`' for c in sorted(admises))}; an annotation of "
            f"your own goes in `meta` (carried as is, never read)")


def residus_warning(schema: Any) -> Optional[str]:
    """Ce que le schéma porte ENCORE d'inconnu — stocké avant la fermeture du
    vocabulaire, donc toléré tant qu'on n'y touche pas. Dit au lecteur comme à
    l'auteur : le lecteur doit savoir laquelle de deux clés fait foi (`options`, pas
    l'`enum` d'à côté), l'auteur que la modifier sera refusé."""
    trouvees, font_foi, ombres = [], set(), []
    # oto#124 : `unknown_columns` stocké a sa propre phrase — il est encore LU jusqu'au
    # 21/10/2026 et la plateforme le retire elle-même (`scripts/retirer_unknown_columns`).
    retire = (isinstance(schema, dict) and reglages.UNKNOWN_COLUMNS in schema)
    for niveau, chemin, noeud in parcours(schema):
        for cle in inconnues(niveau, noeud):
            if retire and not chemin and cle == reglages.UNKNOWN_COLUMNS:
                continue
            trouvees.append(f"`{nom_du_chemin(chemin)}.{cle}`" if chemin
                            else f"`{cle}` (top level)")
            # Ce qui fait foi : la cousine admise, quand elle est posée À CÔTÉ.
            if sk.FAUTES_CONNUES.get(cle) in noeud:
                font_foi.add(sk.FAUTES_CONNUES[cle])
        # Cas du 14/10/2026 : une clé ADMISE rangée dans `meta` a l'air posée et ne l'est pas.
        meta = noeud.get(sk.META)
        if isinstance(meta, dict):
            ombres.extend(f"`{nom_du_chemin(chemin)}.meta.{c}`" if chemin
                          else f"`meta.{c}` (top level)"
                          for c in sorted(set(meta) & (sk.ADMISES[niveau] - {sk.META})))
    phrase_ombres = (
        f"this schema stores in `meta` keys that their level allows: "
        f"{', '.join(ombres[:8])}{', …' if len(ombres) > 8 else ''}. In `meta`, "
        f"they have NO EFFECT (nobody reads it) — an `options` stored this way "
        f"constrains nothing. Set them next to `meta`." if ombres else None)
    if not trouvees:
        fin = (reglages.residu_unknown_columns(schema[reglages.UNKNOWN_COLUMNS]) + "."
               if retire else None)
        return " ".join(p for p in (phrase_ombres, fin) if p) or None
    anciens = [k for k in reglages.ANCIENS if isinstance(schema, dict) and k in schema]
    msg = (f"this schema carries keys that no level allows, set before the "
           f"vocabulary was closed (2026-10-01): {', '.join(trouvees[:8])}"
           + (", …" if len(trouvees) > 8 else "")
           + ". Oto does not read them; they stay stored as long as nobody touches them, "
             "and modifying them is refused. A help text goes in `description`, an "
             "annotation in `meta`, the rest is removed.")
    if font_foi:
        msg += (" What is authoritative: " + ", ".join(f"`{k}`" for k in sorted(font_foi))
                + " — the unknown key next to it is a leftover, whatever it says.")
    if anciens:
        msg += f" For {', '.join(f'`{k}`' for k in anciens)}: {reglages.residu(schema)}."
    if retire:
        msg += " " + reglages.residu_unknown_columns(schema[reglages.UNKNOWN_COLUMNS]) + "."
    if phrase_ombres:
        msg += " " + phrase_ombres
    return msg
