"""CLIQUET de vocabulaire : « doctrine » ne peut que RECULER dans `oto_mcp/`.

Décision d'Alexis du 28/08/2026 (#519) : le produit ne dit plus « doctrine » — il
dit **guide** (ADR 0042, le guide = primitive unique d'instruction) et
**procédure** pour ce qui s'exécute. Le lot A a retiré le mot de l'INTERNE :
modules, symboles, variables, commentaires, docstrings non servies, docs.

Ce qui reste ne reste PAS par oubli. Chaque occurrence encore là est un **alias de
compatibilité** : un nom qui SORT du serveur et qu'on ne peut pas renommer sans
casser un appelant qui vit hors de ce dépôt (dashboard, extension, CLI, fronts
partenaires, flotte d'agents) ou sans toucher une base PARTAGÉE prod/preprod.
Leur retrait est le lot B, avec préavis et date écrite.

Pourquoi un cliquet et pas une règle de revue. Parce qu'une règle de vocabulaire
ne survit pas à six mois : le mot revient par un copier-coller depuis un fichier
voisin, ou par un module neuf qui imite son aîné. Le compte, lui, ne discute pas.

Ce que le cliquet garde exactement :

1. **Aucun fichier hors allowlist ne porte le mot.** Un module neuf qui l'emploie
   rougit tout de suite — c'est le cas le plus probable, et le seul qui ferait
   repartir la dérive à zéro.
2. **Un fichier de l'allowlist ne peut pas en porter PLUS.** Le plafond est ce
   qui était mesuré à la fin du lot A. Ajouter une occurrence à un fichier qui en
   a déjà, c'est agrandir la dette qu'on est en train de rembourser.
3. **Un plafond qui n'est plus atteint est PÉRIMÉ.** Quand le lot B retire des
   alias, il baisse le plafond dans le même commit — sinon la marge libérée se
   remplirait en silence. Le test le dit et nomme le nouveau nombre.

⚠️ Le compte est en `doctrine` INSENSIBLE À LA CASSE, sur le fichier ENTIER
(`DoctrineView` compte, `oto_admin_doctrine` compte, `-- doctrine` en SQL compte).
Pas de subtilité de tokenisation : un cliquet qu'on peut contourner en changeant
la casse ou en passant par un commentaire ne garde rien.

Régénérer les plafonds après un lot qui en RETIRE :

    python - <<'EOF'
    import pathlib, re
    for p in sorted(pathlib.Path("oto_mcp").rglob("*")):
        if not p.is_file() or "__pycache__" in p.parts:
            continue
        try:
            n = len(re.findall("doctrine", p.read_text(encoding="utf-8"), re.I))
        except (UnicodeDecodeError, OSError):
            continue
        if n:
            print(f'    "{p}": {n},')
    EOF
"""
from __future__ import annotations

import pathlib
import re

RACINE = pathlib.Path(__file__).resolve().parents[1] / "oto_mcp"
MOT = re.compile("doctrine", re.I)

# ── Plafonds relevés à la fin du lot A de #519 (2026-08-28) ──────────────────
# Chaque entrée porte la RAISON pour laquelle le mot y survit. Une entrée sans
# raison servie n'a rien à faire ici : elle se renomme.
PLAFONDS: dict[str, int] = {
    # — Chemins REST servis `/api/[me/]doctrines/…` + les deux handlers publics
    #   dont le NOM est figé dans `tests/api/api_routes_table.txt`.
    "oto_mcp/api/public.py": 7,
    "oto_mcp/api/routes.py": 6,
    # — Clés de capacité servies : `org.doctrine.*`, `admin.doctrine`.
    "oto_mcp/capabilities/__init__.py": 2,
    # — Outil MCP servi `oto_admin_doctrine` + sa description.
    "oto_mcp/capabilities/admin_console.py": 5,
    # — Clé de réponse `doctrine` + description servie.
    "oto_mcp/capabilities/agent_context.py": 4,
    # — Clé de réponse `doctrine_ref_count`.
    "oto_mcp/capabilities/connectors/selection.py": 2,
    # — Clé de réponse `run_doctrine`.
    "oto_mcp/capabilities/datastore/activity.py": 1,
    # — Docstrings de modèles `Output` (servies dans `/openapi.json`) + descriptions.
    "oto_mcp/capabilities/groups/core.py": 3,
    # — Clés de réponse `doctrine` / `doctrine_version` + descriptions servies.
    "oto_mcp/capabilities/groups/guide.py": 13,
    # — Chemins `/api/me/doctrines/*`, code d'erreur `unknown_doctrine`, clé
    #   `doctrines`, descriptions servies.
    "oto_mcp/capabilities/guide_library.py": 21,
    # — Description servie.
    "oto_mcp/capabilities/org_console.py": 1,
    # — Clé de réponse `doctrine` + docstring de `RunRow` (servie en OpenAPI).
    "oto_mcp/capabilities/org_monitoring.py": 4,
    # — Le gros morceau : clés `doctrine`/`doctrines`/`doctrine_id`/`group_doctrine`,
    #   capacités `org.doctrine.*`, chemin `/api/me/doctrines/{doctrine_id}`, code
    #   `unknown_doctrine`, kind d'ownership `doctrine`, modèles `DoctrineView` et
    #   `DoctrineMeta` (noms de schéma dans `/openapi.json`), descriptions servies.
    "oto_mcp/capabilities/orgs/instructions.py": 61,
    # — Paramètre servi `doctrine_id` + descriptions.
    "oto_mcp/capabilities/procedure_console.py": 8,
    # — Kind d'ownership `doctrine` + colonne `runs.doctrine` + description.
    "oto_mcp/capabilities/projects.py": 3,
    # — Kind de ressource `doctrine` (valeur en base, `resource_grants`), motif
    #   `doctrine_needs_org_owner`, descriptions servies.
    "oto_mcp/capabilities/resources.py": 11,
    # — Descriptions servies.
    "oto_mcp/capabilities/tools_me.py": 2,
    # — Valeur d'énumération servie `missing_doctrine` (+ le commentaire qui la décrit).
    "oto_mcp/capabilities/usage.py": 2,
    # — Emploi GÉNÉRIQUE dans une doc de connecteur servie au catalogue.
    "oto_mcp/connectors/docs/ahrefs.md": 1,
    # — `ALTER TABLE doctrine_library` (nom d'objet en base).
    "oto_mcp/db/_init.py": 1,
    # — `resource_type = 'doctrine'` + clé `doctrine_version` d'un JSON de nœud.
    "oto_mcp/db/nodes.py": 2,
    # — DDL FIGÉ (base partagée prod/preprod ; le renommage est additif, lot B) :
    #   table `doctrine_library`, ses index, colonne `runs.doctrine`, valeur
    #   `missing_doctrine`, et les commentaires SQL qui les décrivent.
    "oto_mcp/db/schema/connectors.py": 1,
    "oto_mcp/db/schema/orgs.py": 1,
    "oto_mcp/db/schema/procedures.py": 14,
    "oto_mcp/db/schema/projects.py": 1,
    "oto_mcp/db/schema/runs.py": 4,
    "oto_mcp/db/schema/usage.py": 2,
    # — Valeur de kind `doctrine` dans la table des familles de grant.
    "oto_mcp/db/shell.py": 2,
    # — Colonne `runs.doctrine`, clé `doctrine_version` des args journalisés,
    #   alias SQL `AS doctrine`/`AS doctrines` (donc clés de réponse).
    "oto_mcp/db/usage.py": 31,
    # — Nom de table `doctrine_library` dans l'inventaire des colonnes de sub.
    "oto_mcp/db/users.py": 1,
    # — Prose d'onboarding SERVIE à l'agent.
    "oto_mcp/discovery.py": 1,
    # — Le paramètre servi `doctrine` de `run_start`, nommé dans le docstring.
    "oto_mcp/guide_run.py": 1,
    # — Nom de table `doctrine_library` + nom du script de seed.
    "oto_mcp/guides/talent-sourcing/README.md": 3,
    # — Paramètre servi `doctrine=` de `run_start` dans un exemple.
    "oto_mcp/guides/talent-sourcing/talent-sourcing.md": 1,
    # — Bloc d'instructions SERVI au `initialize` + index servi dans la
    #   description d'`oto_procedure` + lecture de la clé `doctrine`.
    "oto_mcp/instructions.py": 7,
    # — Nom de table `doctrine_library`.
    "oto_mcp/org_store/__init__.py": 1,
    # — Kind d'ownership `doctrine` + `resource_type='doctrine'`.
    "oto_mcp/org_store/instructions.py": 2,
    # — Table `doctrine_library` dans le SQL.
    "oto_mcp/org_store/library.py": 8,
    # — Le kind `doctrine` enregistré au registre des ressources possédées.
    "oto_mcp/ownership.py": 1,
    # — Colonne `runs.doctrine` + clé de réponse `doctrines`.
    "oto_mcp/project_audit.py": 2,
    # — Primer SERVI (catalogue des concepts spine).
    "oto_mcp/providers/__init__.py": 1,
    # — Arg tracé `doctrine_version` (écrit dans `tool_calls.args`) + `runs.doctrine`.
    "oto_mcp/server.py": 3,
    # — Emploi GÉNÉRIQUE dans une description servie.
    "oto_mcp/tools/gocardless.py": 1,
    # — Paramètre servi `doctrine` de `run_start`, ses clés de réponse
    #   `doctrine`/`doctrine_version`, et le docstring SERVI comme description.
    "oto_mcp/tools/guide_run.py": 17,
    # — Emploi GÉNÉRIQUE dans une description servie.
    "oto_mcp/tools/unipile.py": 1,
}


def _comptes() -> dict[str, int]:
    out: dict[str, int] = {}
    for p in sorted(RACINE.rglob("*")):
        if not p.is_file() or "__pycache__" in p.parts:
            continue
        try:
            texte = p.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue          # binaire : rien à lire, rien à compter
        n = len(MOT.findall(texte))
        if n:
            out[p.relative_to(RACINE.parent).as_posix()] = n
    return out


def test_aucun_fichier_neuf_ne_reprend_le_mot():
    intrus = sorted(set(_comptes()) - set(PLAFONDS))
    assert not intrus, (
        f"« doctrine » apparaît dans {len(intrus)} fichier(s) qui n'en portaient pas : "
        f"{intrus}. Le produit dit **guide** (ADR 0042) et **procédure** pour ce qui "
        "s'exécute — cf. #519. Si ce fichier sert VRAIMENT un nom historique à un "
        "client hors dépôt, ajoute-le à `PLAFONDS` avec la raison ; sinon, renomme.")


def test_aucun_fichier_nen_porte_davantage():
    comptes = _comptes()
    hausses = {f: (comptes[f], PLAFONDS[f])
               for f in comptes if f in PLAFONDS and comptes[f] > PLAFONDS[f]}
    assert not hausses, (
        f"Le mot regagne du terrain : {hausses} (actuel, plafond). Ces fichiers "
        "portent des alias de compatibilité qu'on rembourse au lot B — on n'en "
        "ajoute pas. Emploie « guide » ou « procédure », ou passe par une constante "
        "nommée si tu dois écrire le nom SERVI une fois de plus.")


def test_un_plafond_devenu_trop_haut_se_rabaisse():
    """Le cliquet ne tient que si la marge libérée ne peut pas se remplir."""
    comptes = _comptes()
    perimes = {f: (comptes.get(f, 0), PLAFONDS[f])
               for f in PLAFONDS if comptes.get(f, 0) < PLAFONDS[f]}
    assert not perimes, (
        f"{len(perimes)} plafond(s) ne sont plus atteints : {perimes} (actuel, "
        "plafond). Baisse-les dans CE commit — un plafond au-dessus du réel est de "
        "la place libre pour la prochaine occurrence. Un fichier tombé à 0 sort de "
        "`PLAFONDS`. Recette dans le docstring.")
