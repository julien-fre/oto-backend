"""Inventaire MCP : aucune capacité du client lemlist ne reste hors des tools.

Côté oto-core, `test_lemlist_coverage.py` prouve que le CLIENT vise les 141
routes documentées. Ça ne dit rien de l'exposition : une méthode que personne
n'appelle est du code mort du point de vue d'un agent, et le trou ne se voit
nulle part — le connecteur a l'air complet, l'agent ne peut pas s'en servir.

Ce test ferme la seconde moitié de la chaîne : toute méthode du client qui
construit un chemin HTTP doit être appelée par au moins un tool. Les rares
exceptions sont NOMMÉES ci-dessous avec leur raison — jamais tolérées en
silence, ce qui est tout l'intérêt d'une liste écrite plutôt que d'un seuil.
"""
from __future__ import annotations

import inspect
import re

import pytest


#: Méthodes qui atteignent l'API mais qu'aucun tool n'appelle, et pourquoi.
NON_EXPOSEES = {
    # Ancienne création de lead (email DANS le chemin). lemlist ne la documente
    # plus ; `create_lead` la remplace et c'est elle qu'expose le connecteur.
    "add_lead": "route héritée, remplacée par create_lead",
    # Une seule page de campagnes. Les tools passent par `list_all_campaigns`,
    # qui l'appelle en boucle et rend en plus le drapeau de troncature.
    "list_campaigns": "atteinte via list_all_campaigns",
    # Export CSV historique `/campaigns/{id}/export`. `lemlist_get_leads` s'en
    # sert INDIRECTEMENT via `get_all_leads` ; l'exposer en direct rendrait un
    # CSV brut là où le tool rend des lignes.
    "export_leads": "atteint via get_all_leads (lemlist_get_leads)",
    # Le geste doux de `delete_lead`, que `lemlist_lead(op="unsubscribe")`
    # obtient en passant `action=None` — un seul chemin d'appel, pas deux.
    "unsubscribe_lead": "alias de delete_lead sans action",
    # Compteurs dérivés d'une page d'activités, plafonnés à 1000 : remplacés par
    # `get_campaign_stats_v2` et gardés pour compatibilité.
    "get_campaign_stats": "déprécié au profit de get_campaign_stats_v2",
    # Composites locaux au-dessus de get_campaign/get_sequences : ils n'ajoutent
    # pas de route, et `lemlist_sequence(op="get")` rend déjà la matière.
    "get_campaign_tree": "composite local (get_campaign + get_sequences)",
    "sync_campaign": "composite local, écrit un fichier côté serveur",
    "save_campaign_tree": "écrit un fichier sur le serveur — jamais exposé",
    "get_sequence_steps": "composite local au-dessus de get_sequences",
}


def _client_source_methods() -> dict[str, str]:
    from oto.tools.lemlist import client as lm
    src = inspect.getsource(lm.LemlistClient)
    # Découpe la classe en méthodes : nom -> corps jusqu'à la prochaine def.
    parts = re.split(r"\n    (?:@\w+\n    )*def ", src)
    out = {}
    for part in parts[1:]:
        name = part.split("(", 1)[0].strip()
        out[name] = part
    return out


def _methods_that_reach_the_api() -> set[str]:
    """Méthodes PUBLIQUES qui construisent un appel HTTP, directement ou en
    déléguant à une voisine qui le fait."""
    methods = _client_source_methods()
    reaching = {n for n, body in methods.items()
                if "self._request(" in body or "requests.get(" in body}
    # Une délégation atteint l'API tout autant, et elle s'empile : `sync_campaign`
    # → `get_campaign_tree` → `get_campaign`. Point fixe, sinon la profondeur 2
    # passe au travers et l'inventaire ment dans le sens rassurant.
    grew = True
    while grew:
        grew = False
        for name, body in methods.items():
            if name in reaching:
                continue
            if any(f"self.{d}(" in body for d in reaching):
                reaching.add(name)
                grew = True
    return {n for n in reaching if not n.startswith("_")}


def _methods_called_by_tools() -> set[str]:
    from oto_mcp.tools import lemlist, lemlist_crm
    called: set[str] = set()
    for module in (lemlist, lemlist_crm):
        src = inspect.getsource(module)
        # Toute MENTION compte, pas seulement un appel direct : les tables de
        # dispatch (`{"add": client.add_unsubscribe, …}[op](email)`) et les
        # affectations (`mark = client.mark_lead_interested if …`) référencent la
        # méthode sans la parenthèse, et les compter autrement ferait crier le
        # garde-fou à tort — un garde-fou qui crie à tort finit ignoré.
        called |= set(re.findall(r"client\.(\w+)", src))
    return called


def test_toute_capacite_du_client_est_atteignable_par_un_tool():
    manquantes = sorted(
        _methods_that_reach_the_api() - _methods_called_by_tools()
        - set(NON_EXPOSEES))
    assert not manquantes, (
        f"{len(manquantes)} méthode(s) du client lemlist qu'aucun tool "
        f"n'appelle : {manquantes}. Expose-les, ou déclare-les dans "
        "NON_EXPOSEES avec la raison.")


def test_la_liste_dexceptions_ne_survit_pas_a_son_objet():
    """Une exception qui ne correspond plus à rien fait mentir la liste."""
    inconnues = sorted(set(NON_EXPOSEES) - _methods_that_reach_the_api())
    assert not inconnues, (
        f"exception(s) sur une méthode qui n'atteint plus l'API : {inconnues}")


def test_les_tools_nappellent_que_des_methodes_qui_existent():
    """Garde version-skew au grain de la méthode, sur les DEUX modules."""
    from oto.tools.lemlist import LemlistClient
    fantomes = sorted(
        m for m in _methods_called_by_tools()
        if not hasattr(LemlistClient, m))
    assert not fantomes, f"méthodes appelées mais absentes du client : {fantomes}"


@pytest.mark.parametrize("tool", [
    "lemlist_campaign_start", "lemlist_launch_lead", "lemlist_inbox_send",
    "lemlist_campaign_auto_review",
])
def test_tout_ce_qui_arme_ou_declenche_un_envoi_est_masque_par_defaut(tool):
    """Le contrat de sûreté du connecteur, énuméré plutôt que raconté."""
    from oto_mcp.tool_visibility import DEFAULT_HIDDEN_TOOLS
    assert tool in DEFAULT_HIDDEN_TOOLS
