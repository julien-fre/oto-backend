"""Chaîne capitalistique française — remonter au groupe, descendre aux filiales.

Signal #337. Le besoin est de qualifier l'INDÉPENDANCE d'une entreprise, et il naît
d'un faux négatif coûteux : dans une campagne, 4 leads sur 5 ont été écartés parce que
l'INSEE les classait « grande entreprise » — c'étaient des FILIALES, petites en propre.
`categorie_entreprise` est calculée par l'INSEE sur le périmètre GROUPE, pas sur
l'entité ; sans la chaîne, elle fait mentir tout ciblage par la taille.

## Ce que l'amont sait réellement rendre (vérifié le 2026-08-28, pas déduit)

- **Enfant → parent** : le RNE publie les mandataires PERSONNES MORALES avec leur
  SIREN. C'est le même bloc `dirigeants` que rend déjà `fr_get` — l'arête existait,
  elle n'était pas assemblée.
- **Parent → enfants** : l'index plein texte de recherche-entreprises **indexe les
  dirigeants**. Son OpenAPI le dit (« q : termes pour une recherche textuelle
  (dénomination et/ou adresse, dirigeants, élus) ») et le différentiel le prouve :
  `q=LEFEBVRE SARRUT` rend FLS IMMOBILIER, EDITIONS LEGISTATIVES et SOCIETE CIVILE
  ARVIL, dont aucun nom ne partage un token avec la requête. C'est l'index inversé que
  le signal demandait de construire côté serveur : il existait déjà en amont.
- **Ce que l'amont NE sait PAS faire** : chercher par le SIREN d'un dirigeant.
  `q=602060147` ne rend que Hachette Livre elle-même, et `nom_personne` ne vise que les
  personnes PHYSIQUES (0 résultat sur « LEFEBVRE SARRUT », vérifié). L'index rend donc
  des CANDIDATS sur le nom ; c'est nous qui prouvons le lien, par le SIREN du
  mandataire. Sur `q=HACHETTE LIVRE`, 5 candidats sur 29 n'ont aucun lien avec elle
  (L'ESPRIT LIVRE, MATRA HACHETTE…) : sans cette vérification, ils passeraient pour
  des filiales.
- **Bornes dures de l'amont** : `per_page` ≤ 25 (26 → HTTP 400) et `page × per_page`
  ≤ 10 000 (401 → HTTP 400). Sur un grand groupe (BOUYGUES = 1 476 candidats),
  l'exhaustivité descendante est donc hors d'atteinte — et se DIT.

## Ce que ce module refuse de deviner

Le registre des bénéficiaires effectifs est fermé au public depuis le 2026-07-31 :
l'actionnariat réel d'une SAS n'est plus accessible. Une SAS sans mandataire personne
morale n'est donc PAS « indépendante », elle est **indéterminée** — et le dire est le
cœur de l'outil, pas son défaut. Relevé sur EDITIONS PAYOT ET RIVAGES : aucun
mandataire personne morale hors commissaire aux comptes, alors qu'Actes Sud la détient
à 100 %.

Aucune écriture, aucun crédit : trois sources open data, en lecture.
"""
from __future__ import annotations

from typing import Literal, Optional

from fastmcp import FastMCP
from mcp.shared.exceptions import McpError
from mcp.types import ErrorData, INVALID_PARAMS

# --- Taxonomie des qualités de mandataire ------------------------------------
# Relevé du 2026-08-28 sur Hachette Livre, Lefebvre Sarrut, Frojal, Calmann-Lévy,
# Payot & Rivages. Le classement est un CONTRAT rendu à l'appelant (le client exige
# des sources opposables), pas une heuristique de confort.

# Un auditeur n'est ni un actionnaire ni un dirigeant : sans cette exclusion, KPMG,
# Deloitte, RSM et Salustro Reydel deviennent la tête de groupe de la moitié de leurs
# clients et contaminent toute la chaîne (le signal cite le cas CERALP / Bamboo).
_CAC = "commissaire aux comptes"

# `forte` — la qualité IMPLIQUE la détention : ces mentions ne sont portées que par un
# associé au capital (SNC, SCS, sociétés civiles).
_FORTE = ("associé commandité", "associée commanditée", "associé indéfiniment",
          "associée indéfiniment", "associé unique", "associée unique")
# `moyenne` — mandat social : la gouvernance est PROUVÉE, le contrôle seulement suggéré
# (une SAS peut être présidée par une société qui n'en détient pas une action).
_MOYENNE = ("président", "présidente", "administrateur", "administratrice",
            "gérant", "gérante", "directeur général", "directrice générale",
            "directoire", "conseil de surveillance", "représentant permanent")
# `faible` — ni détention ni contrôle. Être MEMBRE d'un GIE professionnel (Hachette
# Livre l'est du GIE PROLIVRE, de la CENTRALE DE L'ÉDITION, du CELF) ou LIQUIDATEUR
# d'une coquille n'est pas une appartenance de groupe.
_FAIBLE = ("membre", "liquidateur", "liquidatrice", "autre")

# Seules ces deux bandes se TRAVERSENT : suivre un lien faible ferait remonter d'un
# adhérent vers son GIE, puis du GIE vers tous ses autres membres — un faux groupe.
_TRAVERSABLES = ("forte", "moyenne")
_RANG = {"forte": 3, "moyenne": 2, "faible": 1, "inconnue": 0}

_CAVEAT = (
    "Le registre des bénéficiaires effectifs est fermé au public depuis le 31/07/2024 : "
    "l'actionnariat d'une SAS n'est plus publié. L'absence de mandataire personne morale "
    "ne prouve donc PAS l'indépendance — elle est INDÉTERMINÉE. Ne jamais lire "
    "`confiance=\"indeterminee\"` comme « entreprise indépendante »."
)
_METHODE = (
    "mandataires personnes morales du RNE (recherche-entreprises) ; commissaires aux "
    "comptes exclus ; liens `faible`/`inconnue` rendus mais non traversés"
)

# Garde-fous de fan-out : un parcours en largeur sur un conglomérat explose vite, et
# chaque nœud est un appel amont. Bornes hautes, jamais silencieuses (`tronque`).
_MAX_DEPTH_DUR = 6
_MAX_NOEUDS = 60
_MAX_PAGES_DUR = 20
_PER_PAGE = 25  # maximum amont, vérifié : 26 → HTTP 400


def _bad(msg: str) -> McpError:
    return McpError(ErrorData(code=INVALID_PARAMS, message=msg))


def confiance_du_lien(qualite: Optional[str]) -> str:
    """Bande de confiance d'une qualité de mandataire personne morale.

    L'ordre des tests EST la règle : « Gérant et associé indéfiniment et solidairement
    responsable » porte les deux vocabulaires, et c'est la mention de DÉTENTION qui
    l'emporte. Une qualité non répertoriée sort `inconnue` — jamais promue par défaut,
    parce qu'un lien inventé se propage à toute la chaîne.
    """
    q = (qualite or "").casefold()
    if any(m in q for m in _FORTE):
        return "forte"
    if any(m in q for m in _MOYENNE):
        return "moyenne"
    if any(m in q for m in _FAIBLE):
        return "faible"
    return "inconnue"


def _mandataires(fiche: dict) -> tuple[list[dict], int, int]:
    """(mandataires personnes morales exploitables, CAC écartés, sans SIREN écartés)."""
    gardes, cac, sans_siren = [], 0, 0
    for d in fiche.get("dirigeants") or []:
        if d.get("type_dirigeant") != "personne morale":
            continue
        if _CAC in (d.get("qualite") or "").casefold():
            cac += 1
            continue
        if not d.get("siren"):
            sans_siren += 1
            continue
        gardes.append(d)
    return gardes, cac, sans_siren


def register(mcp: FastMCP) -> None:
    from ..fod import fr as fod_fr  # même proxy FOD que `fr_get` (ADR 0028)

    entreprises = fod_fr.entreprises

    def _fiche(siren: str) -> Optional[dict]:
        """Fiche d'identité amont, ou None si le répertoire ne la reconnaît pas.

        ⚠️ Le client amont a un repli qui rend le PREMIER résultat quand le SIREN exact
        est absent de la page — un homonyme passerait pour l'entreprise demandée et
        toute la chaîne partirait sur la mauvaise branche. On revérifie donc le SIREN
        ici, chez l'appelant, plutôt que d'espérer une correction en amont.
        """
        fiche = entreprises.get_by_siren(siren)
        if not isinstance(fiche, dict) or fiche.get("siren") != siren:
            return None
        return fiche

    def _fiche_ou_refus(siren: str) -> dict:
        fiche = _fiche(siren)
        if fiche is None:
            raise _bad(f"SIREN {siren} inconnu du répertoire des entreprises.")
        if not fiche.get("nom_complet"):
            # Coquille rendue par l'amont (relevé sur 999999999) : SIREN réservé, radié
            # sans dénomination, ou unité non diffusible. Deviner un nom ici enverrait
            # la recherche descendante sur une entreprise sans rapport.
            raise _bad(
                f"SIREN {siren} : le répertoire n'en rend aucune dénomination "
                "(SIREN inconnu, ou unité non diffusible).")
        return fiche

    def _ascendant(racine: str, fiche: dict, max_depth: int) -> dict:
        from concurrent.futures import ThreadPoolExecutor

        liens: list[dict] = []
        tetes: list[dict] = []
        fiches = {racine: fiche}
        vus = {racine: 0}
        parent: dict[str, tuple[str, str]] = {}
        non_resolus: list[str] = []
        non_classees: set[str] = set()
        cac = sans_siren = 0
        cycle = tronque = False
        frontiere = [racine]
        profondeur = 0

        while frontiere and profondeur < max_depth:
            profondeur += 1
            suivants: list[str] = []
            for src in frontiere:
                pms, n_cac, n_sans = _mandataires(fiches[src])
                cac += n_cac
                sans_siren += n_sans
                traversables = 0
                for d in pms:
                    conf = confiance_du_lien(d.get("qualite"))
                    if conf == "inconnue":
                        non_classees.add(d.get("qualite") or "")
                    traverse = conf in _TRAVERSABLES
                    cible = d["siren"]
                    liens.append({
                        "de": src, "vers": cible,
                        "denomination_vers": d.get("denomination"),
                        "qualite": d.get("qualite"), "confiance": conf,
                        "traverse": traverse, "profondeur": profondeur,
                    })
                    if not traverse:
                        continue
                    traversables += 1
                    if cible in vus:
                        cycle = True          # la boucle est rendue, pas suivie
                    elif len(vus) >= _MAX_NOEUDS:
                        tronque = True
                    else:
                        vus[cible] = profondeur
                        parent[cible] = (src, conf)
                        suivants.append(cible)
                # Une tête = un nœud EXPLORÉ, hors racine, sans aucun lien traversable.
                # La racine n'en est jamais une : sans parent, elle est indéterminée.
                if traversables == 0 and src != racine:
                    tetes.append(src)
            # Un étage se lit EN PARALLÈLE : chaque nœud est un aller-retour amont, et
            # une holding en compte volontiers trois par niveau. En série, le budget de
            # 60 nœuds vaudrait une minute d'attente — le tool serait juste, et
            # inutilisable. Même parade que `_fr_profile` dans `tools/fr.py`.
            with ThreadPoolExecutor(max_workers=8) as pool:
                lues = list(zip(suivants, pool.map(_fiche, suivants)))
            for cible, f in lues:
                if f is None:
                    non_resolus.append(cible)   # ex. société étrangère, hors RNE
                else:
                    fiches[cible] = f
            frontiere = [c for c in suivants if c in fiches]
            if suivants and not frontiere:
                break
        if frontiere:
            tronque = True   # il restait des nœuds à explorer au bord du budget

        def _chemin(node: str) -> tuple[list[str], str]:
            chemin, conf = [node], "forte"
            while node in parent:
                node, c = parent[node]
                chemin.append(node)
                conf = c if _RANG[c] < _RANG[conf] else conf
            return list(reversed(chemin)), conf

        rendus = []
        for t in tetes:
            chemin, conf = _chemin(t)
            rendus.append({
                "siren": t,
                "denomination": (fiches.get(t) or {}).get("nom_complet"),
                "profondeur": vus[t], "confiance": conf, "chemin": chemin,
            })
        # La confiance GLOBALE se calcule sur tous les nœuds atteints, pas sur les
        # seules têtes : un parcours arrêté par `max_depth` (ou refermé par un cycle)
        # n'a pas de tête, et rendre `indeterminee` dans ce cas ferait lire « aucun
        # actionnaire publié » là où on en a trouvé trois. Relevé en réel sur
        # Calmann-Lévy à max_depth=1 (3 parents, 0 tête).
        atteints = [n for n in vus if n != racine]
        globale = (max((_chemin(n)[1] for n in atteints), key=lambda c: _RANG[c])
                   if atteints else "indeterminee")
        out = {
            "op": "ascendant", "siren": racine,
            "denomination": fiche.get("nom_complet"),
            "categorie_entreprise": fiche.get("categorie_entreprise"),
            "tetes": rendus, "liens": liens, "confiance": globale,
            "cycle": cycle, "tronque": tronque,
            "exclus": {"commissaires_aux_comptes": cac, "sans_siren": sans_siren},
            "appels_amont": len(fiches) + len(non_resolus),
            "methode": _METHODE, "caveat": _CAVEAT,
        }
        if non_resolus:
            out["parents_hors_repertoire"] = non_resolus
        if non_classees:
            out["qualites_non_classees"] = sorted(non_classees)
        return out

    def _descendant(racine: str, fiche: dict, max_pages: int) -> dict:
        denomination = fiche["nom_complet"]
        requete = fiche.get("nom_raison_sociale") or denomination
        filiales: list[dict] = []
        examines = 0
        total_amont = None
        for page in range(1, max_pages + 1):
            res = entreprises.search(query=requete, page=page, per_page=_PER_PAGE)
            lot = res.get("results") or []
            total_amont = res.get("total_results")
            examines += len(lot)
            for cand in lot:
                if cand.get("siren") == racine:
                    continue
                pms, _cac, _sans = _mandataires(cand)
                # Le lien n'est PROUVÉ que par le SIREN du mandataire ; le nom du
                # candidat ne prouve rien (l'index est plein texte et flou).
                liens = [d for d in pms if d.get("siren") == racine]
                if not liens:
                    continue
                meilleur = max(liens,
                               key=lambda d: _RANG[confiance_du_lien(d.get("qualite"))])
                filiales.append({
                    "siren": cand.get("siren"),
                    "denomination": cand.get("nom_complet"),
                    "qualite": meilleur.get("qualite"),
                    "confiance": confiance_du_lien(meilleur.get("qualite")),
                    "categorie_entreprise": cand.get("categorie_entreprise"),
                    "etat_administratif": cand.get("etat_administratif"),
                })
            if len(lot) < _PER_PAGE:
                break
        filiales.sort(key=lambda f: (-_RANG[f["confiance"]], f["siren"]))
        tronques = bool(total_amont is not None and examines < total_amont)
        return {
            "op": "descendant", "siren": racine, "denomination": denomination,
            "requete": requete,
            "filiales": filiales, "total": len(filiales),
            "candidats_examines": examines, "candidats_total_amont": total_amont,
            "candidats_tronques": tronques,
            "methode": (
                f"candidats de l'index plein texte amont sur « {requete} » "
                "(qui indexe les dirigeants), retenus SEULEMENT si un mandataire "
                f"personne morale porte le SIREN {racine} ; " + _METHODE),
            "caveat": (
                (f"{examines} candidats examinés sur {total_amont} : l'inventaire est "
                 "un ÉCHANTILLON, pas une liste exhaustive — relancer avec max_pages "
                 "plus haut. ") if tronques else "") + _CAVEAT,
        }

    @mcp.tool()
    def fr_groupe(
        siren: str,
        op: Literal["ascendant", "descendant"] = "ascendant",
        max_depth: int = 4,
        max_pages: int = 4,
    ) -> dict:
        """Chaîne capitalistique d'une entreprise française — qualifier son
        INDÉPENDANCE, ou inventorier un groupe.

        Pourquoi : `categorie_entreprise` (PME/ETI/GE) est calculée par l'INSEE sur le
        périmètre GROUPE, jamais sur l'entité — une filiale minuscule sort en "GE".
        Ce tool sépare les deux.

        `op`:
        - **"ascendant"** (défaut) : remonte les mandataires personnes morales, en
          LARGEUR (une société a souvent plusieurs parents au même niveau). Rend
          `{tetes, liens, confiance, cycle, tronque}`.
        - **"descendant"** : entités contrôlées par ce SIREN — un appel au lieu de N.

        `confiance` par lien : **forte** = la qualité implique la détention (associé
        commandité / indéfiniment responsable) · **moyenne** = mandat social (président,
        administrateur, gérant) : gouvernance prouvée, contrôle suggéré · **faible** =
        ni l'un ni l'autre (membre de GIE, liquidateur) · **inconnue** = qualité non
        répertoriée. Seuls forte et moyenne sont traversés. Les commissaires aux
        comptes sont exclus (comptés dans `exclus`).

        ⚠️ `confiance="indeterminee"` (aucun mandataire personne morale) ne veut PAS
        dire « indépendante » : le registre des bénéficiaires effectifs est fermé au
        public depuis le 31/07/2024, donc l'actionnaire d'une SAS n'est pas publié.
        ⚠️ En descendant, l'amont plafonne à 25 résultats/page et 10 000 au total :
        `candidats_tronques=true` signale un échantillon, pas un inventaire.
        Aucune filiale / aucun parent est une RÉPONSE, pas une erreur.

        Args:
            siren: SIREN de l'entreprise (9 chiffres).
            op: ascendant (défaut) | descendant.
            max_depth: op="ascendant" — étages remontés (défaut 4, max 6).
            max_pages: op="descendant" — pages de 25 candidats examinées (défaut 4,
                max 20).
        """
        digits = "".join(c for c in str(siren) if c.isdigit())
        if len(digits) != 9:
            raise _bad(f"SIREN invalide : {siren!r} — 9 chiffres attendus.")
        if op not in ("ascendant", "descendant"):
            raise _bad("op doit être 'ascendant' ou 'descendant'")
        fiche = _fiche_ou_refus(digits)
        if op == "ascendant":
            return _ascendant(digits, fiche, max(1, min(max_depth, _MAX_DEPTH_DUR)))
        return _descendant(digits, fiche, max(1, min(max_pages, _MAX_PAGES_DUR)))


__all__ = ["register", "confiance_du_lien"]
