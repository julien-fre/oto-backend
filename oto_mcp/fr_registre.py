"""Lecture du registre des personnes derrière `fr_directors` (#612).

**Une liste vide qui ne dit pas POURQUOI elle est vide est un silence.** Trois
causes distinctes produisaient exactement la même réponse `[]`, mesurées le
2026-09-01 sur la surface servie :

1. **SIREN inconnu.** `entreprises.get_directors` appelle `get_by_siren` et rend
   `[]` quand l'identité est absente — un numéro erroné est indistinguable d'une
   entreprise sans dirigeant. Vérifié : `fr_directors("000000000")` → `[]`.
2. **Forme juridique qui ne déclare personne au registre.** Une association rend
   la même liste vide qu'une société sans dirigeant déclaré. Vérifié :
   LE SOUVENIR FRANCAIS (`775676182`, catégorie juridique 9220) → `[]`, et
   COMMUNE DE MARSEILLE (`211300553`, 7210) → `[]`.
3. **Entrepreneur individuel.** Le registre rend bien la personne, mais avec
   `qualite: null` — exact (il n'inscrit pas de qualité pour un EI) et lu par un
   agent comme « aucun rôle », donc comme « aucun dirigeant ». Vérifié :
   `478464803` (1000) → `[{"nom": "DAURE", …, "qualite": null}]`.

Le coût terrain (campagne du 29/08/2026, issue #612) : la ligne-piège d'un jalon
est « aucune personne physique nommée au registre ». Cent lignes lues sur la
seule liste vide, ce sont cent faux positifs — l'association et le SIREN mort
comptent comme des sociétés opaques.

Ce module ne fait donc **que nommer la cause** : il ne devine rien sur
l'entreprise, il dit ce que le REGISTRE peut ou ne peut pas déclarer pour sa
forme juridique, et laisse l'appelant conclure.

## La classification, et où elle s'arrête

`nature_juridique` est la catégorie juridique INSEE de **niveau III** (4
chiffres) servie par Recherche Entreprises. Son **premier chiffre** est le
niveau I de la nomenclature, et c'est le seul étage sur lequel on tranche :
les familles 1/5/6 sont immatriculées au registre national des entreprises,
2/7/8/9 ne le sont pas. Les familles **3** (droit étranger) et **4** (droit
public à activité commerciale) sont mixtes — une succursale ou un EPIC peut
être au RCS, pas ses voisins de famille : elles restent **indéterminées**, et
c'est ce que la réponse dit, plutôt que de trancher dans le sens rassurant.
"""
from __future__ import annotations

from typing import Any, Optional

# Nomenclature INSEE des catégories juridiques, NIVEAU I (1er chiffre du code de
# niveau III). Libellés officiels — on ne descend pas au niveau III : il faudrait
# embarquer la table complète, et aucune des questions posées ici n'en dépend.
_FORME_NIVEAU_I: dict[str, str] = {
    "1": "Entrepreneur individuel",
    "2": "Groupement de droit privé non doté de la personnalité morale",
    "3": "Personne morale de droit étranger",
    "4": "Personne morale de droit public soumise au droit commercial",
    "5": "Société commerciale",
    "6": "Autre personne morale immatriculée au RCS",
    "7": "Personne morale et organisme soumis au droit administratif",
    "8": "Organisme privé spécialisé",
    "9": "Groupement de droit privé",
}

# Les trois états possibles du registre pour une forme juridique. Trois, et pas
# deux : « on ne sait pas » n'est pas « il n'y a rien ».
REGISTRE_ATTENDU = "attendu"
REGISTRE_HORS = "hors_registre"
REGISTRE_INDETERMINE = "indetermine"

_REGISTRE_NIVEAU_I: dict[str, str] = {
    # Immatriculées au RNE ⟹ le registre y déclare des personnes, et une liste
    # vide EST un signal sur l'entreprise.
    "1": REGISTRE_ATTENDU,   # EI, immatriculé au RNE depuis 2023 (mesuré : rend la personne)
    "5": REGISTRE_ATTENDU,   # sociétés commerciales (RCS → RNE)
    "6": REGISTRE_ATTENDU,   # le libellé de la famille DIT « immatriculée au RCS »
    # Hors registre ⟹ la liste vide ne dit RIEN sur l'entreprise.
    "2": REGISTRE_HORS,      # pas de personnalité morale, donc pas d'immatriculation
    "7": REGISTRE_HORS,      # État, collectivités, établissements publics (mesuré : commune → [])
    "8": REGISTRE_HORS,      # organismes privés spécialisés
    "9": REGISTRE_HORS,      # associations, fondations, syndicats — RNA, pas RNE (mesuré → [])
    # 3 et 4 sont ABSENTES volontairement : familles mixtes, cf. docstring du module.
}

_NOTE: dict[str, str] = {
    REGISTRE_ATTENDU: (
        "forme juridique immatriculée au registre national des entreprises, et "
        "aucun dirigeant n'y est déclaré."
    ),
    REGISTRE_HORS: (
        "forme juridique NON immatriculée au registre national des entreprises : "
        "le registre ne déclare de personne pour aucune entreprise de cette forme. "
        "Cette liste vide ne dit rien de l'entreprise — ne pas la lire comme "
        "« aucun dirigeant »."
    ),
    REGISTRE_INDETERMINE: (
        "forme juridique dont une partie seulement des entreprises est immatriculée "
        "au registre (droit étranger, droit public à activité commerciale) : une "
        "liste vide n'y est pas concluante. Vérifier l'immatriculation avant de "
        "conclure."
    ),
}

# La qualité que le registre n'inscrit PAS pour un entrepreneur individuel, et
# que la réponse pose explicitement — en disant qu'elle est déduite.
QUALITE_EI = "Entrepreneur individuel"
_QUALITE_EI_SOURCE = (
    "déduite de la catégorie juridique 1000 — le registre n'inscrit pas de "
    "qualité pour un entrepreneur individuel"
)


def famille(nature_juridique: Any) -> str:
    """Premier chiffre de la catégorie juridique — `""` si absente/illisible."""
    code = str(nature_juridique or "").strip()
    return code[:1] if code[:1].isdigit() else ""


def _qualifier(dirigeants: list[dict], fam: str) -> list[dict]:
    """Pose la qualité de l'entrepreneur individuel, et la DIT déduite.

    Ailleurs qu'en famille 1, on ne touche à rien : une qualité absente y est un
    fait du registre, pas un trou à combler.
    """
    if fam != "1":
        return [dict(d) for d in dirigeants]
    out: list[dict] = []
    for brut in dirigeants:
        d = dict(brut)
        if not d.get("qualite") and d.get("type_dirigeant") == "personne physique":
            d["qualite"] = QUALITE_EI
            d["qualite_deduite"] = _QUALITE_EI_SOURCE
        out.append(d)
    return out


def fiche(siren: str, identity: Optional[dict]) -> dict:
    """Une entrée de réponse pour UN siren, à partir de son identité amont.

    `identity` est la fiche Recherche Entreprises (`entreprises.get_by_siren`),
    ou `None` quand le numéro est inconnu — et c'est ce `None` qui devient un
    `not_found` NOMMÉ, au lieu de la liste vide qu'il produisait.
    """
    if not identity:
        return {"error": "not_found", "siren": siren}

    fam = famille(identity.get("nature_juridique"))
    registre = _REGISTRE_NIVEAU_I.get(fam, REGISTRE_INDETERMINE)
    dirigeants = _qualifier(identity.get("dirigeants") or [], fam)
    physiques = [d for d in dirigeants if d.get("type_dirigeant") == "personne physique"]

    out = {
        "siren": identity.get("siren") or siren,
        "nom_complet": identity.get("nom_complet"),
        "nature_juridique": str(identity.get("nature_juridique") or "") or None,
        "forme": _FORME_NIVEAU_I.get(fam),
        "registre": registre,
        "dirigeants": dirigeants,
        # La question du terrain est « une personne physique est-elle NOMMÉE ? » :
        # une société dont le seul dirigeant est une autre société vaut 0 ici.
        "personnes_physiques": len(physiques),
    }
    if not dirigeants:
        out["note"] = _NOTE[registre]
    return out


# Les sept catégories de `synthese`, mutuellement exclusives et exhaustives : une
# fiche tombe dans une et une seule. Un comparateur à deux catégories rendrait
# « pas de dirigeant » pour cinq d'entre elles.
_CATEGORIES = (
    "avec_personne_physique",
    "dirigeant_personne_morale_seulement",
    "aucun_dirigeant_declare",
    "forme_sans_dirigeant_au_registre",
    "registre_indetermine",
    "not_found",
    "erreur",
)


def categorie(f: dict) -> str:
    """La catégorie de synthèse d'une fiche — l'ordre des tests EST le contrat."""
    if f.get("error") == "not_found":
        return "not_found"
    if f.get("error"):
        return "erreur"
    if f.get("personnes_physiques"):
        return "avec_personne_physique"
    if f.get("dirigeants"):
        return "dirigeant_personne_morale_seulement"
    registre = f.get("registre")
    if registre == REGISTRE_ATTENDU:
        return "aucun_dirigeant_declare"
    if registre == REGISTRE_HORS:
        return "forme_sans_dirigeant_au_registre"
    return "registre_indetermine"


def synthese(fiches: list[dict]) -> dict:
    """Compte les fiches par catégorie — toutes les clés, y compris les zéros.

    Les zéros sont rendus exprès : une clé absente se lit « pas mesuré », un 0 se
    lit « mesuré, aucun ».
    """
    compte = {k: 0 for k in _CATEGORIES}
    for f in fiches:
        compte[categorie(f)] += 1
    return compte
