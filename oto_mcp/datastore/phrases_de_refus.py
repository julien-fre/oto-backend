"""Ce qu'un refus DIT à l'agent — la moitié actionnable, séparée du contrôle.

Quatre aides de prose, et une seule raison d'être ensemble : **le refus est la
surface la plus efficace de la plateforme, et la plus gaspillée.** Un agent qui
exécute une procédure écrite par un autre n'a jamais lu le schéma ; le refus est
souvent le seul texte qu'il verra sur cette colonne. Mesuré : un refus qui nomme le
geste exact est suivi dans la minute, un refus brut fait rejouer le même appel.

- `_forme_attendue` — ce que la colonne ACCEPTE, en une clause ;
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

from typing import Any

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
        bouts.append("une valeur parmi " + " | ".join(options))
    elif ftype in (None, "text"):
        bouts.append("du texte libre")
    else:
        bouts.append(f"une valeur de type `{ftype}`")
    ml = max_length_of(field)
    if ml:
        bouts.append(f"≤ {ml} caractères")
    motif = pattern_of(field)
    if motif:
        bouts.append(f"de motif `{motif}`")
    return ", ".join(bouts)


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
        bouts.append(f"`{champ}` vaut {valeurs}")
    return " (requis quand " + " et ".join(bouts) + ")"


def _clause_un_seul_appel(rw: Any) -> str:
    """oto-backend#649 : POUR REVENIR EN ARRIÈRE, il ne suffit pas de vider ce champ.

    `required_when` se juge sur la ligne FINALE, aiguillage compris (#347) : vider
    ce champ pendant que l'aiguillage vaut encore la valeur qui l'exige refuse cet
    appel-là, précisément parce que rien n'a changé côté aiguillage. Un agent qui
    corrige en deux temps (vider, puis changer l'aiguillage — ou l'inverse) se fait
    refuser au premier des deux, quel que soit l'ordre : c'est le MÊME appel qui
    doit porter les deux écritures."""
    if not isinstance(rw, dict) or not rw:
        return ""
    noms = ", ".join(f"`{c}`" for c in rw)
    return (f" ; pour revenir en arrière, vide ce champ ET change {noms} dans le "
            f"MÊME appel — vider l'un sans l'autre reste refusé, quel que soit "
            f"l'ordre")


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
    return (f" ; ne l'écris pas dans {noms}, qui n'accepte que "
            + ("ces valeurs" if len(fermes) > 1 else "les valeurs ci-dessus"))
