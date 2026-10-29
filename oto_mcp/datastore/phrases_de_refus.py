"""Ce qu'un refus DIT à l'agent — la moitié actionnable, séparée du contrôle.

Quatre aides de prose, et une seule raison d'être ensemble : **le refus est la
surface la plus efficace de la plateforme, et la plus gaspillée.** Un agent qui
exécute une procédure écrite par un autre n'a jamais lu le schéma ; le refus est
souvent le seul texte qu'il verra sur cette colonne. Mesuré : un refus qui nomme le
geste exact est suivi dans la minute, un refus brut fait rejouer le même appel.

- `_forme_attendue` — ce que la colonne ACCEPTE, en une clause ; `gabarit`, sa forme
  courte, tient la place de la valeur dans la charge à renvoyer (oto#135) ;
- `_gated_by` — quelle colonne en rend une autre requise, **dérivé de la
  déclaration**, jamais deviné par ressemblance de nom ;
- `_cause_required_when` — POURQUOI c'est requis, en français plutôt qu'en `repr` ;
- `_clause_aiguillage` — où le texte NE va PAS, pour éviter le second aller-retour.

Séparé de `validation.py` parce que ce sont deux métiers : là-bas on décide si ça
passe, ici on écrit à qui de droit ce qu'il faut faire. Les deux changent pour des
raisons différentes — une règle bouge quand le produit bouge, une phrase bouge quand
on mesure qu'elle n'est pas suivie.

⚠️ **Ce module LIT des attributs de colonne** (`options`, `type`, `required_when`) :
il est donc listé dans `vocabulaire._read_keys`. L'oublier ferait rétrécir ce que la
plateforme déclare interpréter, et l'avertissement accuserait des clés parfaitement
lues — sans qu'aucun banc ne rougisse.
"""
from __future__ import annotations

from difflib import get_close_matches
from typing import Any, Optional

from .declaration import max_length_of, pattern_of


def _forme_attendue(field: dict) -> str:
    """Ce qu'une colonne ACCEPTE, dit en une clause (#545).

    Un refus qui nomme la colonne sans dire sa forme fait relire le schéma — et un
    agent qui exécute une procédure écrite par un autre ne l'a jamais lu. Dérivé des
    fonctions qui APPLIQUENT (`max_length_of`, `pattern_of`), jamais d'une copie de
    leurs conditions : une borne mal déclarée est muette ici comme elle l'est là."""
    bouts: list[str] = []
    options = [str(o) for o in (field.get("options") or [])]
    ftype = field.get("type")
    if options:
        bouts.append("one value among " + " | ".join(options))
    elif ftype in (None, "text"):
        bouts.append("free text")
    elif ftype == "ref":
        # La cible est dans la déclaration : la nommer dit où lire l'`_id`.
        bouts.append(f"the `_id` of a row of table {field.get('target')} — never its "
                     f"name")
    elif ftype in _FORME_DU_TYPE:
        bouts.append(_FORME_DU_TYPE[ftype])
    else:
        bouts.append(f"a value of type `{ftype}`")
    ml = max_length_of(field)
    if ml:
        bouts.append(f"≤ {ml} characters")
    motif = pattern_of(field)
    if motif:
        bouts.append(f"matching pattern `{motif}`")
    return ", ".join(bouts)


#: Les types dont le NOM ne dit pas la forme — `_forme_attendue` la dit en clair.
_FORME_DU_TYPE = {
    "phone": ("a phone number — preferably international: `+`, country code, "
              "number (E.164); spaces and dashes accepted"),
}

#: Le mot du gabarit, par type déclaré — la forme courte de `_forme_attendue`.
_MOT_DU_TYPE = {None: "text", "text": "text", "number": "number", "bool": "true | false",
                "date": "date YYYY-MM-DD", "datetime": "ISO 8601 date-time",
                "url": "http(s) URL", "email": "email",
                "phone": "phone, +country code preferred", "ref": "row _id",
                "json": "json",
                "enum": "value", "object": "object", "list": "list"}


def gabarit(field: dict) -> Any:
    """Ce que le refus met À LA PLACE de la valeur dans la charge à renvoyer (oto#135) :
    `"<text>"`, `"<number>"`, `"<a | b>"`, avec la borne et le motif quand il y en a.

    Un gabarit et pas un exemple : un exemple serait recopié à la lettre — mesuré, les
    agents reproduisent la forme montrée —, un `<…>` ne se confond avec aucune valeur.
    Un objet rend ses sous-champs REQUIS, puisque c'est eux qu'il faut écrire. Dérivé
    des mêmes lecteurs que `_forme_attendue` (`max_length_of`, `pattern_of`)."""
    ftype = field.get("type")
    if ftype == "object":
        requis = {str(x["key"]): gabarit(x) for x in (field.get("fields") or [])
                  if isinstance(x, dict) and x.get("key") and x.get("required")}
        if requis:
            return requis
    options = [str(o) for o in (field.get("options") or [])]
    bouts = [" | ".join(options) if options else _MOT_DU_TYPE.get(ftype, str(ftype))]
    if ftype == "ref" and not options and field.get("target") is not None:
        bouts[0] = f"row _id of table {field.get('target')}"
    ml = max_length_of(field)
    if ml:
        bouts.append(f"≤ {ml} characters")
    motif = pattern_of(field)
    if motif:
        bouts.append(f"pattern {motif}")
    return "<" + ", ".join(bouts) + ">"


def cle_la_plus_proche(cle: str, candidates: list) -> Optional[str]:
    """Le nom déclaré qui ressemble le plus à `cle`, ou `None` : une faute de frappe
    (`emial`) a une destination, un nom sans parent n'en a pas — et un pointeur inventé
    enverrait écrire là où rien n'attend la valeur."""
    trouve = get_close_matches(cle, [str(c) for c in candidates], n=1, cutoff=0.6)
    return trouve[0] if trouve else None


def _gated_by(fields: list) -> dict:
    """`{colonne qui sert de CONDITION: [colonnes qu'elle rend requises]}` (#545).

    C'est ce qui rend le pointeur DÉRIVÉ et non deviné : `retraitement_motif` est
    désignée comme destination du texte libre parce qu'elle déclare `required_when`
    SUR `retraitement`, pas parce qu'un nom ressemble à un autre. Sans relation
    déclarée, aucun pointeur — un pointeur inventé enverrait écrire dans une colonne
    qui n'attend rien, ce qui est pire que se taire."""
    out: dict = {}
    for f in fields:
        if not isinstance(f, dict) or not f.get("key"):
            continue
        rw = f.get("required_when")
        if not (isinstance(rw, dict) and rw):
            continue
        for condition in rw:
            out.setdefault(str(condition), []).append(f)
    return out


def _cause_required_when(rw: Any) -> str:
    """POURQUOI ce champ est requis, en français plutôt qu'en `repr` Python (#545).

    Le refus rendait la condition telle quelle — `(requis quand {'retraitement':
    ['injoignable', 'hors_cible']})`. C'est lisible pour qui connaît déjà le schéma,
    donc pour personne dans le cas qui compte : un agent qui exécute une procédure
    écrite par un autre. La condition est la moitié actionnable du refus — elle dit
    quelles valeurs de l'aiguillage arment la contrainte."""
    if not isinstance(rw, dict) or not rw:
        return ""
    bouts = []
    for champ, attendu in rw.items():
        valeurs = (" | ".join(str(x) for x in attendu)
                   if isinstance(attendu, (list, tuple)) else str(attendu))
        bouts.append(f"`{champ}` is {valeurs}")
    return " (required when " + " and ".join(bouts) + ")"


def _clause_un_seul_appel(rw: Any) -> str:
    """oto-backend#649 : POUR REVENIR EN ARRIÈRE, il ne suffit pas de vider ce champ.

    `required_when` se juge sur la ligne MERGÉE, aiguillage compris (#347) : vider
    ce champ pendant que l'aiguillage porte encore la valeur qui l'exige refuse cet
    appel-là, précisément parce que rien n'a changé côté aiguillage. Le geste qui
    passe est un `data_write` UNIQUE portant les deux écritures — c'est tout ce que
    la clause nomme.

    ⚠️ Elle ne promet PAS l'indifférence à l'ordre, qui serait fausse : en DEUX
    appels, changer l'aiguillage d'abord passe (la garde s'éteint sur la ligne
    mergée), et le vidage qui suit passe aussi — rien ne refuse un champ gaté resté
    rempli quand la garde tombe. La contrainte porte sur le VIDE tant que la garde
    tient, jamais sur le PLEIN quand elle s'éteint."""
    if not isinstance(rw, dict) or not rw:
        return ""
    noms = ", ".join(f"`{c}`" for c in rw)
    return (f"; to go back, clear this field AND change {noms} in the "
            f"SAME call — clearing this field alone, while the condition still "
            f"holds, remains refused")


def _clause_aiguillage(fields: list, rw: Any) -> str:
    """La PRÉVENTION du geste suivant : ne pas écrire le texte dans l'aiguillage.

    Le refus arrive au seul moment où il est actionnable, et il vaut mieux qu'il dise
    tout de suite les deux moitiés : où va le texte, et où il n'ira pas. Sans elle,
    l'agent corrige en écrivant le motif DANS l'énuméré, se fait refuser une seconde
    fois, et paie deux allers-retours pour une ligne. Muet quand l'aiguillage n'est
    pas une énumération déclarée — il n'y a alors rien à opposer."""
    if not isinstance(rw, dict):
        return ""
    par_cle = {str(x.get("key")): x for x in fields
               if isinstance(x, dict) and x.get("key")}
    fermes = [str(c) for c in rw
              if (par_cle.get(str(c)) or {}).get("options")]
    if not fermes:
        return ""
    noms = ", ".join(f"`{c}`" for c in fermes)
    return (f"; do not write it in {noms}, which only accepts "
            + ("these values" if len(fermes) > 1 else "the values above"))
