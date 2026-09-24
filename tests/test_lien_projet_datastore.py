"""Un lien de projet vers un tableau sert `datastore`, pas seulement `namespace`.

⚠️ **C'était le dernier endroit du produit où l'ancien nom était servi SEUL.** Partout
ailleurs la bascule du 09/09/2026 sert les deux clés, et le retrait commun
(`RETRAIT_DATASTORE`, 08/11/2026) enlèvera simplement le doublon. Ici il n'y avait pas de
doublon à enlever : ce lieu serait donc devenu soit le dernier `namespace` servi de la
plateforme, soit une rupture sèche pour ses lecteurs le jour du retrait.

Mesuré au moment du correctif : **322 liens `tableau` dans 112 projets** en production.

Le correctif est purement ADDITIF — aucun consommateur ne casse aujourd'hui — et il
range ce lieu sous la même échéance que le reste.
"""
from __future__ import annotations

from oto_mcp.db.projects import _apply_tableau_names, _apply_tableau_name_ids


def test_le_chemin_par_ID_sert_le_nom_du_tableau():
    """Le chemin du dashboard : `target_ref` est l'id numérique du tableau.

    ⚠️ Le doublon `namespace` posé le 09/09 a été RETIRÉ le 10/09 : le renommage était
    incohérent — liste basculée à sec, réponses unitaires doublées, upload jamais
    basculé — et l'incohérence coûtait plus que la rupture."""
    liens = [{"target_type": "tableau", "target_ref": "174"}]
    _apply_tableau_names(liens, {174: "un-tableau"})
    assert liens[0]["datastore"] == "un-tableau"
    assert "namespace" not in liens[0], "un seul nom, partout"


def test_le_chemin_par_NOM_sert_le_nom_du_tableau():
    """Le chemin de l'agent (#117) : `target_ref` EST déjà le nom du tableau."""
    liens = [{"target_type": "tableau", "target_ref": "un-vivier"}]
    _apply_tableau_names(liens, {})                      # rien à résoudre par id
    _apply_tableau_name_ids(liens, {"un-vivier": 12})    # résolu dans la portée (#365)
    assert liens[0]["datastore"] == "un-vivier"
    assert "namespace" not in liens[0]


def test_un_tableau_DISPARU_ne_pose_aucune_des_deux():
    """C'est ce qui fait le lien mort : l'audit conclut sur l'ABSENCE de la clé. Poser
    `datastore` vide ferait passer un lien mort pour vivant."""
    liens = [{"target_type": "tableau", "target_ref": "999"}]
    _apply_tableau_names(liens, {})
    assert "datastore" not in liens[0] and "namespace" not in liens[0]


def test_le_second_chemin_se_declenche_sur_la_cle_NEUVE():
    """⚠️ Le chemin par nom ne s'applique qu'aux liens non encore résolus : il teste
    `datastore_id`, que le premier chemin pose. Sinon il repasserait sur un lien déjà
    résolu."""
    deja = [{"target_type": "tableau", "target_ref": "174",
             "datastore": "resolu-par-id", "datastore_id": 174}]
    _apply_tableau_name_ids(deja, {"174": 9})
    assert deja[0]["datastore"] == "resolu-par-id", "un lien déjà résolu n'est pas réécrit"
    assert deja[0]["datastore_id"] == 174


def test_aucun_lecteur_interne_ne_depend_du_nom_qui_DISPARAIT():
    """⚠️ Le banc qui protège la date de retrait. Quatre lecteurs internes lisaient
    `l["namespace"]` sur un lien — l'audit des liens morts, le calcul des tableaux liés
    et les deux étiquettes du partage. Le 08/11/2026 ils auraient cessé de voir quoi que
    ce soit, **en silence** : un lien vivant serait passé pour mort, un tableau lié pour
    non lié, et le hint aurait proposé de lier ce qui l'est déjà — à chaque appel et pour
    tout le monde.

    Ce défaut a DÉJÀ été payé une fois, dans l'autre sens (note du 08/09 dans
    `tools/datastore.py`) : lire `datastore` quand seul `namespace` était posé rendait un
    ensemble vide. C'est le même piège, symétrique, et il se rejouera à la date de
    retrait si un lecteur reste accroché.

    ⚠️ **La sonde est BORNÉE aux fichiers qui consomment des liens de projet**, et elle
    ignore les lectures de lignes SQL (`r["namespace"]` = la colonne `user_datastores`,
    délibérément conservée pour le rollback). Sans cette borne elle crierait sur les
    quatre AUTRES sens du mot — flottes du runner, familles d'outils MCP, vocabulaire
    système — et un avertissement qui crie à tort est celui qu'on apprend à ignorer.
    """
    import pathlib
    import re
    racine = pathlib.Path(__file__).resolve().parents[1] / "oto_mcp"
    fichiers = [f for f in racine.rglob("*.py")
                if "list_project_links" in f.read_text()]
    assert len(fichiers) >= 4, (
        f"la sonde ne trouve que {len(fichiers)} consommateur(s) de liens : elle ne "
        f"regarde plus au bon endroit")

    coupables = []
    for f in fichiers:
        for n, ligne in enumerate(f.read_text().splitlines(), 1):
            # ⚠️ `[\w\]\[0-9]+` et pas `\w+` : le lecteur le plus critique du lot
            # s'écrivait `match[0].get("namespace")` — le crochet coupait la capture, et
            # la sonde passait à côté du chemin `slot:`, celui par lequel TOUTES les
            # procédures adressent leur tableau. Une garde aveugle là où ça compte le
            # plus rassure exactement là où il ne faut pas.
            m = re.search(r'([\w\]\[0-9]+)\.get\("namespace"\)'
                          r'|([\w\]\[0-9]+)\["namespace"\]', ligne)
            if not m:
                continue
            var = m.group(1) or m.group(2)
            if var in {"r", "row", "cur", "d"}:
                continue              # une LIGNE SQL, pas un lien
            if "RETRAIT_DATASTORE" in ligne:
                continue              # la POSE du doublon, datée et assumée
            coupables.append(f"{f.relative_to(racine)}:{n}  ({var})")
    assert not coupables, (
        f"ces lecteurs de LIENS s'accrochent à la clé qui disparaît le 08/11/2026 : "
        f"{coupables}. Lire `datastore`, posée dans le même geste.")
