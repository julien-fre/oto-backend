"""Le DDL assemblé est GELÉ : découper `_schema.py` ne devait rien changer à la chaîne.

Le DDL vivait dans un littéral unique de 1 578 lignes ; il vit maintenant dans
`db/schema/<domaine>.py`, que `_schema.ASSEMBLAGE` concatène dans un ordre figé.
Le déplacement était PUR — pas un espace de différence — et c'est exactement ce
qu'un déplacement de fichiers ne prouve pas tout seul : la seule preuve possible
est l'empreinte de la chaîne servie.

Ce que le gel protège, une fois le déplacement fait :

1. **Une base PARTAGÉE prod/preprod** (`docs/live-migrations.md`) : le DDL exécuté
   au boot preprod s'applique instantanément à la production, qui tourne encore
   l'ancien code. Une altération accidentelle du DDL n'a pas de fenêtre de rattrapage.
2. **L'ORDRE est une contrainte d'exécution**, pas une mise en page : PostgreSQL
   crée les tables dans l'ordre du DDL, et une FK vers une table pas encore créée
   échoue sur une base VIERGE (#151 sur `orgs`, `tenants` avant `orgs` en L1,
   `grants` avant `grant_counters` en L4). Un simple réordonnancement de la liste
   d'assemblage — le genre de geste qu'un tri alphabétique « propre » produit —
   casserait tout premier boot, et rien d'autre ne le verrait.
3. **Un fragment orphelin est silencieux** : une constante déclarée dans un module
   de domaine mais absente de `ASSEMBLAGE` ne lève aucune erreur ; ses tables
   n'existent simplement jamais.

⚠️ **Ce hash se met à jour À LA MAIN**, dans le commit qui change le DDL, jamais
séparément. Un changement de DDL légitime le fait échouer : c'est voulu — il rend
l'écriture du DDL délibérée et visible en revue, au même titre qu'un `ALTER` de
`_init.py`. Recalculer :

    python -c "import hashlib;from oto_mcp.db import _schema;\
print(hashlib.sha256(_schema._SCHEMA.encode()).hexdigest())"
"""
from __future__ import annotations

import hashlib
import re

from oto_mcp.db import _schema, schema

# Empreinte de `_SCHEMA`, mise à jour dans le commit qui change le DDL —
# jamais recopiée d'un côté d'un conflit : deux lots qui touchent le DDL
# la recalculent sur le résultat FUSIONNÉ, sinon la garde valide un DDL que
# personne n'a servi. Cf. l'avertissement du docstring avant de la toucher.
EMPREINTE = "fb06985a542809e80bea6047972e98d272b16504f4560c3d20359ef17a6bdffb"
LONGUEUR = 102487

_CREATE_TABLE = re.compile(r"^CREATE TABLE IF NOT EXISTS (\w+)", re.M)


def test_la_chaine_assemblee_est_celle_qui_est_gelee():
    """La preuve du déplacement pur, et la garde du DDL ensuite."""
    empreinte = hashlib.sha256(_schema._SCHEMA.encode("utf-8")).hexdigest()
    assert (empreinte, len(_schema._SCHEMA)) == (EMPREINTE, LONGUEUR), (
        "le DDL assemblé a changé. Si c'est délibéré (vraie évolution du schéma), "
        "mets à jour EMPREINTE/LONGUEUR dans CE commit. Sinon, c'est qu'un "
        "déplacement de fragment a modifié le SQL — ce qui touche la base PARTAGÉE "
        "prod/preprod au premier boot.")


def test_chaque_table_a_exactement_un_domicile():
    """Un `CREATE TABLE` par domaine, et un seul — sinon le DDL en crée deux, ou
    l'un des deux dérive sans que personne ne le voie."""
    domiciles: dict[str, list[str]] = {}
    for nom_module in schema.__all__:
        module = getattr(schema, nom_module)
        for const in dir(module):
            if const.startswith("_") or not isinstance(getattr(module, const), str):
                continue
            for table in _CREATE_TABLE.findall(getattr(module, const)):
                domiciles.setdefault(table, []).append(f"{nom_module}.{const}")

    doublons = {t: d for t, d in domiciles.items() if len(d) > 1}
    assert not doublons, f"tables déclarées à plusieurs endroits : {doublons}"

    assemblees = _CREATE_TABLE.findall(_schema._SCHEMA)
    assert len(assemblees) == len(set(assemblees)), "table créée deux fois dans l'assemblage"
    assert set(assemblees) == set(domiciles), (
        "écart entre les tables des fragments et celles de l'assemblage : "
        f"orphelines={sorted(set(domiciles) - set(assemblees))}, "
        f"inconnues={sorted(set(assemblees) - set(domiciles))}")


def test_aucun_fragment_ne_reste_hors_de_l_assemblage():
    """Un fragment déclaré mais jamais assemblé ne lève rien : ses tables n'existent
    simplement pas. Le seul endroit où ça se voit est ici."""
    declares = set()
    for nom_module in schema.__all__:
        module = getattr(schema, nom_module)
        for const in dir(module):
            valeur = getattr(module, const)
            if not const.startswith("_") and isinstance(valeur, str) and "CREATE " in valeur:
                declares.add((nom_module, const))

    assembles = set()
    for fragment in _schema.ASSEMBLAGE:
        for nom_module, const in declares:
            if getattr(getattr(schema, nom_module), const) is fragment:
                assembles.add((nom_module, const))

    orphelins = sorted(declares - assembles)
    assert not orphelins, (
        f"fragments de DDL jamais assemblés : {orphelins}. Ils ne créent aucune "
        "table et personne ne s'en apercevrait — ajoute-les à `_schema.ASSEMBLAGE` "
        "à la bonne place (les FK imposent l'ordre) ou supprime-les.")


def test_l_ordre_impose_par_les_fk_est_tenu():
    """Les trois ordres déjà payés en incident, vérifiés sur la chaîne ASSEMBLÉE —
    c'est-à-dire à travers la frontière des modules, là où un déplacement les casse.

    Les tests de lot (`test_tenant_l1_migration`, `test_grants_l4_migration`) les
    gardent aussi ; ils sont répétés ici parce qu'ils sont désormais une propriété
    de l'ORDRE D'ASSEMBLAGE, pas d'un fichier."""
    ddl = _schema._SCHEMA
    for avant, apres, pourquoi in (
        ("tenants", "orgs", "orgs.tenant_id → tenants(id)"),
        ("orgs", "org_members", "org_members.org_id → orgs(id)"),
        ("grants", "grant_counters", "grant_counters → grants(id)"),
        ("docs", "doc_embeddings", "doc_embeddings.doc_id → docs(id)"),
        ("datastore_rows", "datastore_row_embeddings", "FK composite sur la PK"),
    ):
        i = ddl.index(f"CREATE TABLE IF NOT EXISTS {avant}")
        j = ddl.index(f"CREATE TABLE IF NOT EXISTS {apres}")
        assert i < j, (
            f"`{avant}` doit être créée avant `{apres}` ({pourquoi}) : sur une base "
            "VIERGE, PostgreSQL crée les tables dans l'ordre du DDL et la FK échoue.")
