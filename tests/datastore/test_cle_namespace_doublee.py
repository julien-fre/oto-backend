"""Le nom du tableau se sert sous UN seul nom : `datastore`. Le doublon est retiré.

Ce banc gardait l'inverse — la clé `namespace` doublée le temps du préavis — et sa
raison reste vraie, elle mérite d'être lue avant de toucher à ces réponses :

⚠️ **C'était la seule panne MUETTE de la bascule.** Un chemin qui change rend un 404 ou
un 308 : on le voit, on le corrige le jour même. Une clé de réponse qui disparaît ne
rend rien — `r.namespace` vaut `undefined`, sans erreur et sans journal, chez un
consommateur qu'on ne connaît peut-être pas.

**Ce qui a fait retirer le doublon le 10/09/2026 (décision d'Alexis) : l'incohérence
coûtait plus que la rupture.** Trois politiques coexistaient dans le même produit —

    /api/datastores (la liste)   `datastores` SEUL      basculé à sec
    les réponses unitaires       les DEUX noms          doublé
    l'upload de lignes           `namespace` SEUL       jamais basculé

— et nos deux fronts (le nôtre, celui du partenaire) portaient chacun un pont pour
absorber ça. *Un pont qu'on ne retire pas devient permanent, et un pont permanent masque
le renommage suivant.* Un seul nom partout coûte une rupture ANNONCÉE plutôt qu'une dette
permanente répartie sur trois dépôts.

Ce que ce banc garde désormais : **le nom unique est servi PARTOUT**, sans exception —
parce que la prochaine panne muette serait une réponse qui aurait oublié de basculer,
exactement comme l'upload l'avait fait pendant deux jours sans que personne le voie.
"""
from __future__ import annotations

from oto_mcp.datastore.identite import CLE, de_releve, identite


def test_le_nom_est_servi_sous_datastore_et_lui_seul():
    out = identite(42, "vivier")
    assert out["datastore"] == "vivier"
    assert "namespace" not in out, (
        "le doublon est retiré : le garder ferait un second nom permanent")
    assert out[CLE] == 42, "le numéro reste servi — c'est lui qu'on adresse ensuite"


def test_une_reponse_batie_sur_un_RELEVÉ_suit_la_meme_regle():
    # ⚠️ Le relevé porte `ns_id` et `datastore` — pas `id`/`namespace` : c'est la forme
    # que `dernier_tableau` et le `trace` d'une mutation rendent tous deux.
    out = de_releve({"ns_id": 7, "datastore": "vivier"}, "vivier")
    assert out["datastore"] == "vivier" and "namespace" not in out
    assert out[CLE] == 7


def test_une_adresse_NON_résolue_sert_quand_meme_le_nom_reçu():
    """⚠️ Le cas qui compte pour le lecteur : même sans relevé, la réponse porte
    l'adresse telle qu'elle a été reçue. Sa présence est la preuve que le tableau a été
    résolu — la retirer rendrait l'écho impossible à distinguer d'un silence."""
    out = de_releve(None, "vivier")
    assert out["datastore"] == "vivier" and "namespace" not in out


def test_AUCUNE_reponse_du_datastore_ne_sert_encore_l_ancien_nom():
    """⚠️ La garde qui remplace celle du doublon, et elle vise le vrai risque : une
    surface qui n'aurait pas basculé. L'upload de lignes est resté deux jours à
    `namespace` seul pendant que la liste basculait à sec — personne ne l'a vu, parce
    que rien ne comparait les surfaces entre elles.

    **Deux corrections du 10/09/2026, toutes deux nées d'un instrument qui regardait à
    côté :**

    1. La version précédente n'exigeait de rougir que si une réponse portait **les deux**
       clés. Elle ne pouvait donc pas voir ce que son propre texte disait chercher — une
       surface restée à `namespace` SEUL, précisément le cas de l'upload. Elle cherche
       désormais la clé morte, doublée ou non.
    2. Elle ne voyait que les littéraux `{"namespace": …}`. La forme qu'il a fallu
       corriger dans les liens de projet était `l["namespace"] = nm` — une pose par
       indice, invisible pour elle. Les deux formes sont couvertes.

    **Les exclusions nomment un SENS, pas un fichier.** C'est l'autre défaut corrigé :
    `node_view` avait été exclu parce que son module s'appelle ainsi, alors que la clé
    qu'il servait était bien le nom d'un tableau — elle a échappé à la bascule jusqu'au
    soir. Une exclusion qui ne peut pas dire QUEL sens elle protège est une exclusion à
    supprimer.
    """
    import ast
    import pathlib

    # Chaque exclusion dit POURQUOI, et il n'y a que trois raisons valables. Une
    # exclusion qu'on ne sait pas ranger dans l'une des trois est une exclusion à
    # supprimer — c'est ce tri qui manquait quand `node_view` a été écarté sur son nom
    # de module alors qu'il servait bel et bien le nom d'un tableau.
    #
    # (a) AUTRE SENS — le mot n'y désigne pas un tableau. Vérifié en lisant ce que la
    #     valeur vaut, pas ce que le module s'appelle.
    AUTRE_SENS = {
        "capabilities/agent_context.py": "famille d'outils MCP (`_namespace_of(t.name)`)",
        "capabilities/tools_me.py": "famille d'outils MCP (`namespace_of(inp.name)`)",
        "capabilities/audit_log.py": "famille de l'outil appelé (`fr_`, `apollo_`…)",
        "tools/meta.py": "famille d'outils MCP",
        "tools/catalogue.py": "famille d'outils MCP (`namespace_of(t.name)`, oto#170)",
    }
    # (b) VALEUR PERSISTÉE — la clé relit un champ écrit avant la bascule. La renommer
    #     ne renommerait pas la donnée : elle détruirait la correspondance.
    VALEUR_PERSISTEE = {
        "capabilities/uploads.py": "cible figée dans le jeton au moment du mint",
        "deprecations.py": "chemin REST historique, figé par l'alias",
    }
    # (c) MÊME SENS, AUTRE PORTEUR — c'est bien un tableau du datastore, et ça devra
    #     basculer ; ce n'est simplement pas ce lot-ci qui le porte. Signalé à la
    #     session `fleet` le 10/09/2026, après qu'une session cliente a mesuré que la
    #     réponse `runner/fleets` sert un `namespace` qui désigne une table de campagne
    #     (`make_store(...).count_rows(f["namespace"], …)` le prouve). La colonne
    #     `runner_fleets.namespace` et le `payload` des jobs sont persistés : la bascule
    #     y est un geste de LEUR chantier, pas une ligne à changer ici.
    HORS_PERIMETRE = {
        "capabilities/runner_fleets.py": "table cible d'une campagne — session fleet",
        "capabilities/runner_jobs.py": "table cible portée dans le payload — session fleet",
    }
    AUTRES_SENS = {**AUTRE_SENS, **VALEUR_PERSISTEE, **HORS_PERIMETRE}

    racine = pathlib.Path(__file__).resolve().parents[2] / "oto_mcp"
    coupables = []
    for f in racine.rglob("*.py"):
        rel = str(f.relative_to(racine))
        if any(rel.endswith(m) or m in rel for m in AUTRES_SENS):
            continue
        for n in ast.walk(ast.parse(f.read_text())):
            # forme 1 — la clé POSÉE dans un littéral de réponse
            if isinstance(n, ast.Dict) and any(
                    isinstance(k, ast.Constant) and k.value == "namespace"
                    for k in n.keys):
                coupables.append(f"{rel}:{n.lineno} (littéral)")
            # forme 2 — la clé posée APRÈS coup : `out["namespace"] = …`
            cibles = (n.targets if isinstance(n, ast.Assign)
                      else [n.target] if isinstance(n, (ast.AugAssign, ast.AnnAssign))
                      else [])
            for c in cibles:
                if (isinstance(c, ast.Subscript) and isinstance(c.slice, ast.Constant)
                        and c.slice.value == "namespace"):
                    coupables.append(f"{rel}:{n.lineno} (posée par indice)")
    assert not coupables, (
        "ces surfaces servent encore `namespace` pour désigner un TABLEAU — le sens a "
        "basculé à sec le 10/09/2026, sans doublon : "
        f"{sorted(set(coupables))}. Si c'est un AUTRE sens du mot, ajoute le module à "
        "`AUTRES_SENS` en écrivant lequel — une exclusion muette se retourne contre "
        "le prochain renommage.")


def test_la_garde_VOIT_ce_qu_elle_pretend_chercher():
    """Preuve par la chute, en dur : la garde ci-dessus rend zéro, et un zéro ne prouve
    rien tant qu'on n'a pas vu l'instrument rougir. On lui donne les deux formes qu'elle
    doit attraper et on vérifie qu'elle les voit — sinon son zéro décrirait son propre
    aveuglement, pas le dépôt."""
    import ast

    source = ('def f(x):\n'
              '    out = {"datastore": x, "namespace": x}\n'
              '    out["namespace"] = x\n'
              '    return out\n')
    vus = []
    for n in ast.walk(ast.parse(source)):
        if isinstance(n, ast.Dict) and any(
                isinstance(k, ast.Constant) and k.value == "namespace" for k in n.keys):
            vus.append("littéral")
        cibles = (n.targets if isinstance(n, ast.Assign)
                  else [n.target] if isinstance(n, (ast.AugAssign, ast.AnnAssign))
                  else [])
        for c in cibles:
            if (isinstance(c, ast.Subscript) and isinstance(c.slice, ast.Constant)
                    and c.slice.value == "namespace"):
                vus.append("indice")
    assert sorted(vus) == ["indice", "littéral"], (
        f"la garde ne voit pas les deux formes qu'elle annonce : {vus}")
