"""La COLONNE côté Python : ce qu'une écriture touche, et sous quel nom on la désigne.

Extrait du store (#325), déplacement pur. Le pendant Python de `db/paths` : là-bas on
traduit un nom en SQL, ici on décide ce qu'une écriture modifie et ce qu'un ancien nom
désigne encore.

La règle que ce module porte tient en une phrase — **une écriture ne touche QUE ce
qu'elle nomme** — et elle a coûté deux défauts symétriques, l'un après l'autre :

- écrire une VALEUR effaçait l'origine : le patch par identifiant, le geste le plus
  courant d'un agent ;
- écrire une ORIGINE seule effaçait la valeur : le geste nominal du rattrapage de
  socle, quand un tableau adopte les couches après coup.

Deux correctifs symétriques auraient laissé passer le troisième. Une règle unique dont
les deux découlent, non.

S'y ajoute la traduction des anciens noms plats pendant une migration : elle vit ici
parce qu'elle répond à la même question — de quelle colonne parle-t-on ?
"""
from __future__ import annotations

from typing import Any, Optional

from . import datastore_schema as dsv2
from .datastore_errors import RowValidationError

# Les colonnes de la PLATEFORME : elles vivent dans la ligne sans être des
# données de l'utilisateur — ni purgeables, ni écrasables par une écriture.
_META_COLS = ("_id", "_created_at", "_updated_at", "_claimed_by", "_claimed_until",
              "_claims", "_abandon")


def _writes_layers(new: Any) -> bool:
    """L'écriture NOMME-t-elle des couches ? (`{"origine": …}`, `{"valeur": …}`…)

    Strict, comme tout écrivain : un dict fait UNIQUEMENT de couches connues. Un
    `{"a": 1, "origine": "x"}` reste une donnée `json` métier qui se trouve avoir un
    champ nommé « origine » — on ne le réinterprète pas."""
    return (isinstance(new, dict) and bool(new)
            and all(k in dsv2.ALL_LAYER_KEYS for k in new))


def _existing_layers(existing: Any) -> dict:
    """Le contenu ACTUEL d'une colonne, vu comme ses couches.

    Tolérant, comme tout lecteur : un dict qui porte `valeur` est une colonne à
    couches même s'il en porte une qu'on ne connaît pas — écrite par une version plus
    récente, elle traverse intacte au lieu d'être perdue à la première réécriture par
    un nœud plus ancien. Un scalaire est une valeur sans couches ; `None` est le vide."""
    if isinstance(existing, dict) and existing and (
            dsv2.VALUE_LAYER in existing
            or all(k in dsv2.ALL_LAYER_KEYS for k in existing)):
        return dict(existing)
    return {} if existing is None else {dsv2.VALUE_LAYER: existing}


def _to_path(schema: Optional[dict], nom):
    """Un ancien nom plat → son chemin réel ; tout le reste, inchangé."""
    if not isinstance(nom, str):
        return nom
    cible = dsv2.resolve_flat_name(schema, nom)
    if cible is None:
        return nom
    colonne, rang, attr = cible
    return f"{colonne}[{rang}].{attr}"


def _resolve_filters(schema: Optional[dict], filters):
    out = []
    for f in filters or []:
        if not isinstance(f, dict):
            out.append(f)
            continue
        g = dict(f)
        if g.get("field"):
            g["field"] = _to_path(schema, g["field"])
        if isinstance(g.get("fields"), list):
            g["fields"] = [_to_path(schema, k) for k in g["fields"]]
        if isinstance(g.get("where"), list):
            g["where"] = _resolve_filters(schema, g["where"])
        out.append(g)
    return out


def _resolve_metrics(schema: Optional[dict], metrics):
    out = []
    for m in metrics or []:
        if not isinstance(m, dict):
            out.append(m)
            continue
        g = dict(m)
        if g.get("field"):
            g["field"] = _to_path(schema, g["field"])
        if isinstance(g.get("where"), list):
            g["where"] = _resolve_filters(schema, g["where"])
        out.append(g)
    return out


def _resolve_group_by(schema: Optional[dict], group_by):
    if isinstance(group_by, (list, tuple)):
        return [_to_path(schema, k) for k in group_by]
    return _to_path(schema, group_by)


def _refuse_flat_writes(schema: Optional[dict], user_data: dict) -> None:
    """Écrire sur un nom PROJETÉ est refusé, en nommant la cible neuve (oto#22 §6).

    Pendant la migration, `contact1_nom` est servi en LECTURE — calculé depuis la
    colonne-tableau, jamais stocké. L'accepter en écriture créerait une colonne libre
    du même nom : la lecture continuerait de rendre la valeur PROJETÉE, et ce qui vient
    d'être écrit serait invisible tout en ayant été accepté. C'est la forme exacte du
    défaut qu'on passe la journée à fermer — un accusé de réception pour un travail qui
    n'atteint rien.

    Le refus dit où écrire : un message qui dit seulement « non » fait deviner."""
    if not user_data:
        return
    for cle in user_data:
        cible = dsv2.resolve_flat_name(schema, cle)
        if cible is None:
            continue
        colonne, rang, attr = cible
        raise RowValidationError([
            f"{cle}: nom servi en lecture pendant la migration, il ne s'écrit pas "
            f"(il est CALCULÉ depuis `{colonne}`, jamais stocké) — écrire "
            f"`{colonne}[{rang}].{attr}`"])


def _scan_mixed(value: Any, path: str, errors: list) -> None:
    """Le balayage RÉCURSIF de la garde #329 — au grain feuille, parce que c'est
    à l'intérieur des items de colonne-liste (les attributs contacts) que
    passent les écritures réelles. Trois natures de dict, trois traitements :
    mixte (≥1 couche connue + ≥1 inconnue) → refus nommé ; pur-couches → on ne
    descend PAS dedans (la valeur d'une couche est opaque, contrat du lecteur
    tolérant) ; sans aucune couche → donnée libre, on descend (ses feuilles
    peuvent porter des couches)."""
    if isinstance(value, dict):
        cles = set(value)
        couches = cles & set(dsv2.ALL_LAYER_KEYS)
        if couches:
            inconnues = sorted(cles - set(dsv2.ALL_LAYER_KEYS))
            if inconnues:
                errors.append(
                    f"{path}: {', '.join(repr(k) for k in inconnues)} n'est pas une "
                    f"couche — les couches sont {', '.join(dsv2.ALL_LAYER_KEYS)}. "
                    "Rien n'a été écrit. Corrige la clé ; si c'est un objet métier "
                    "qui porte ce nom par coïncidence, déclare la colonne en type "
                    "`json` (data_set_schema) — elle devient exempte de cette garde.")
            return
        for k, v in value.items():
            _scan_mixed(v, f"{path}.{k}", errors)
    elif isinstance(value, list):
        for i, item in enumerate(value):
            _scan_mixed(item, f"{path}[{i}]", errors)


def _refuse_mixed_layers(schema: Optional[dict], user_data: Optional[dict]) -> None:
    """#329 : une couche mal orthographiée se REFUSE, elle n'écrase jamais.

    Un dict qui mêle une clé de couche connue et une inconnue était traité en
    donnée json ordinaire (`_writes_layers` strict + `unknown_layers`
    court-circuité sans `valeur`) : il ÉCRASAIT la valeur existante sans une
    erreur — une faute de frappe systématique dans une procédure effacerait un
    champ sur ~9 000 lignes. La garde vit ICI, à la validation d'entrée, sur le
    payload ÉCRIT : dans le merge elle raterait les items (grain colonne), sur
    le résultat mergé elle bloquerait rétroactivement les lignes porteuses d'un
    dict mixte historique.

    Exemption au grain où un type EST déclaré : une colonne de premier niveau
    déclarée `json` est un objet métier assumé — un contenu y porte `origine`
    sans être des couches."""
    if not user_data:
        return
    exemptes = {f.get("key") for f in dsv2._fields(schema)
                if f.get("type") == "json" and f.get("key")}
    errors: list = []
    for col, val in user_data.items():
        if col in exemptes or col in _META_COLS:
            continue
        _scan_mixed(val, col, errors)
    if errors:
        raise RowValidationError(errors)


def _refuse_dotted_names(user_data: Optional[dict]) -> None:
    """#329 volet 2 : un nom de COLONNE ne porte ni point ni adresse.

    `data_write` avec `"champ.comment"` en clé fabriquait une colonne littérale
    fantôme : acceptée, persistée, et invisible à l'adresse qui la nomme (le
    filtre et le tri lisent la COUCHE `data->'champ'->>'comment'`, jamais la
    colonne littérale) — avec collision silencieuse en lecture. Le refus nomme
    la forme attendue au lieu de dire seulement non."""
    for cle in user_data or {}:
        if "." in cle:
            base = cle.split("[")[0].split(".")[0]
            raise RowValidationError([
                f"`{cle}` n'est pas un nom de colonne — les points désignent des "
                f"couches ou des attributs, qui s'écrivent en forme imbriquée : "
                f'{{"{base}": {{…}}}}. Une colonne littérale nommée `{cle}` serait '
                "invisible au filtre et au tri du même nom. Rien n'a été écrit."])


# ── ce qu'une écriture VIDE (#407/#408/#409) ──────────────────────────────────
#
# Le pendant de la règle du merge. « Une écriture ne touche que ce qu'elle nomme »
# dit ce qui SURVIT ; il restait à dire ce qui TOMBE. Nommer un champ avec `null`
# l'efface — c'est le seul geste qui vide une valeur fausse, donc il reste permis —
# mais il est indiscernable, dans un payload, d'un `None` de sérialisation : une
# variable non peuplée, un gabarit à demi rempli, un aller-retour de lecture.
#
# Vécu le 13/08/2026 (org 226, tableau `edition-essais`) : une session a écrit
# `row={'moteur': None, 'siren': …}` ligne par ligne, a reçu des succès ordinaires,
# et a découvert le champ vidé huit minutes plus tard — en l'imputant à l'écriture
# d'enrichissement suivante, qui ne nommait pas `moteur` et ne l'avait pas touché
# (trois signaux, #407/#408/#409, sur une cause qui n'était pas la leur). L'écriture
# a fait ce qu'on lui demandait ; c'est ce qu'elle en a DIT qui manquait.
#
# Même patron que `hors_schema` (#294) et `hors_options` (#319) : on n'empêche rien,
# on nomme. Et on nomme la VALEUR PERDUE — sans elle il n'y a rien à rétablir.

# Deux bornes, pour qu'un relevé reste lisible par un agent : le nombre
# d'effacements nommés, et la taille d'une valeur rendue. Au-delà, on dit la TAILLE
# plutôt qu'un extrait — un extrait ferait croire qu'on tient la valeur.
_EFFACEMENTS_NOMMES = 20
_VALEUR_RENDUE_MAX = 300


def _valeur_posee(new: Any) -> tuple:
    """`(l'écriture touche-t-elle la VALEUR ?, la valeur qu'elle pose)`.

    Écrire `{"origine": …}` seul ne touche pas la valeur (c'est toute la règle de
    `_merge_column`) : ce n'est donc jamais un effacement, même si la colonne
    finissait vide pour une autre raison."""
    if not _writes_layers(new):
        return True, new
    if dsv2.VALUE_LAYER in new:
        return True, new[dsv2.VALUE_LAYER]
    return False, None


def effacements(existing: Optional[dict], user_data: Optional[dict],
                row_id: Optional[str] = None) -> list[dict]:
    """Les colonnes que ce geste VIDE : il les nomme, avec une valeur vide, là où
    la ligne portait quelque chose. Une par entrée, avec la valeur PERDUE.

    Le vide se juge DÉBALLÉ (`unwrap`) des deux côtés, comme tout ce qui juge une
    valeur : une colonne à couches dont la `valeur` tombe est vidée au même titre
    qu'un scalaire, et une colonne qui ne portait que son `origine` n'avait déjà
    pas de valeur à perdre."""
    sortie: list[dict] = []
    for cle, neuf in (user_data or {}).items():
        if cle in _META_COLS:
            continue
        touche, posee = _valeur_posee(neuf)
        if not touche or not dsv2._is_empty(posee):
            continue
        ancienne = dsv2.unwrap((existing or {}).get(cle))
        if dsv2._is_empty(ancienne):
            continue                      # rien à perdre : on ne fait pas de bruit
        sortie.append({"ligne": row_id, "champ": cle, "valeur": ancienne})
    return sortie


def _valeur_rendue(valeur: Any) -> Any:
    """La valeur perdue, ou sa TAILLE quand la rendre coûterait la réponse."""
    n = len(valeur) if isinstance(valeur, str) else len(str(valeur))
    if n <= _VALEUR_RENDUE_MAX:
        return valeur
    return (f"<{n} caractères — la valeur complète n'est plus lisible ici, "
            "elle n'est plus en base non plus>")


def effacements_report(records: list) -> dict:
    """Le relevé des effacements, prêt à fusionner dans une réponse d'écriture.

    `{}` quand rien n'a été vidé — le cas normal ne porte pas de clé parasite."""
    if not records:
        return {}
    nommes = [{**r, "valeur": _valeur_rendue(r.get("valeur"))}
              for r in records[:_EFFACEMENTS_NOMMES]]
    reste = len(records) - len(nommes)
    hint = ("une valeur vide (`null`, `\"\"`) dans le payload EFFACE la valeur en "
            "place — ce n'est PAS la même chose que ne pas nommer le champ, qui le "
            "laisse intact. Si l'effacement n'était pas voulu (variable non peuplée, "
            "gabarit à demi rempli), réécris les valeurs ci-dessus : elles ne sont "
            "plus en base.")
    if reste:
        hint += f" {len(records)} effacements au total, {len(nommes)} nommés ici."
    return {"valeurs_effacees": nommes, "valeurs_effacees_hint": hint}


def _merge_column(existing: Any, new: Any) -> Any:
    """Fusion d'UNE colonne. **Aucune couche ne s'écrit implicitement, dans aucun sens.**

    Une écriture ne touche QUE ce qu'elle nomme. C'est la protection contre
    l'ACCIDENT, pas contre l'intention — et surtout, c'est ce qui dispense l'agent d'y
    penser : il écrit ce qu'il veut poser, le reste demeure. Un geste explicite
    remplace ce qu'il vise ; il n'y a pas de verrou, donc rien à contourner.

    Les deux directions ont coûté un défaut chacune, et la seconde a failli coûter
    8 910 lignes :

      - écrire une VALEUR effaçait l'origine (#322) — le patch par `id`, le geste le
        plus courant d'un agent ;
      - écrire une ORIGINE seule effaçait la valeur (#326) — le geste nominal du
        RATTRAPAGE de socle, quand un tableau adopte les couches après coup. Aucune
        erreur, la valeur simplement disparue.

    D'où la règle unique dont les deux découlent, plutôt que deux correctifs
    symétriques : on part de l'existant, l'écriture y dépose ce qu'elle nomme.

    ⚠️ Deux conséquences qui ne se devinent pas :

    `comment` et `link` décrivent LA VALEUR : quand elle change sans qu'ils soient
    renommés, ils tombent avec elle — les garder ferait affirmer une provenance
    fausse, précisément le défaut qu'on élimine une couche plus haut. `origine`, elle,
    décrit le point de départ : elle survit.

    Une écriture ORDINAIRE (scalaire, `null`, ou donnée `json`) est une écriture de
    la valeur : elle laisse l'origine intacte. Effacer l'origine se demande —
    `{"origine": null}`. Et une colonne dont il ne reste que la valeur redevient un
    scalaire nu : les lignes sans couches ne doivent pas se mettre à porter une
    enveloppe."""
    if not _writes_layers(new):
        # Toute colonne A une origine ; quand elle est VIDE il n'y a rien à préserver,
        # et la colonne reste plate — le plat est un état, pas une nature.
        origine = _existing_layers(existing).get(dsv2.ORIGIN_LAYER)
        if origine is None:
            return new
        return ({dsv2.ORIGIN_LAYER: origine} if new is None
                else {dsv2.VALUE_LAYER: new, dsv2.ORIGIN_LAYER: origine})
    out = _existing_layers(existing)
    if dsv2.VALUE_LAYER in new:
        for couche in dsv2.VALUE_BOUND_LAYERS:
            out.pop(couche, None)
    out.update(new)
    out = {k: v for k, v in out.items() if v is not None}
    if not out:
        return None
    if set(out) == {dsv2.VALUE_LAYER}:
        return out[dsv2.VALUE_LAYER]
    return out
