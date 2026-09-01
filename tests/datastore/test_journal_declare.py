"""Cliquet : le journal d'activité SERT exactement ce que son contrat déclare.

Même famille que `tests/connectors/test_carte_connecteur_declaree.py` (#667), sur une
autre surface — et le défaut mesuré ici allait dans les DEUX sens, ce qui justifie un
cliquet à deux faces là où celui des connecteurs n'en garde qu'une.

État au 2026-09-01, avant ce fichier : le producteur (`db/usage._ds_activity_entry`)
rendait 17 clés, `ActivityEntry` en déclarait 12, et les deux listes ne se recouvraient
qu'à moitié.

- **Neuf clés servies et tues** — dont `row_id`, `from_status`, `to_status`, `fields`.
  Un front tiers a demandé « `before`/`after`, ou au moins un `row_id` » pour ouvrir
  son bouton d'annulation : les quatre lui étaient déjà envoyées, et le contrat ne les
  nommait pas. Une surface qui sert plus qu'elle ne dit fait redemander ce qu'elle
  donne déjà.
- **Quatre clés promises et jamais rendues** — `call_id`, `at`, `run_doctrine`,
  `run_outcome`, quand le producteur dit `created_at`, `doctrine`, `outcome`. C'est le
  pire des deux sens : le client lit `undefined`, sans erreur, sans log, et croit à une
  donnée absente plutôt qu'à un contrat faux.

⚠️ **Ce qui rend la dérive possible et invisible** : `Capability.Output` DÉCRIT, il ne
valide pas — le handler rend un `dict`, servi tel quel. Déclarer un champ ne le fait
donc pas apparaître, en retirer un ne l'enlève pas du fil. Aucune erreur ne se lève
jamais des deux côtés ; seul un banc qui compare les deux listes voit quelque chose.

Éprouvé rouge avant d'être posé (2026-09-01) : `row_id` retiré d'`ActivityEntry` ⟹
le premier test nomme `row_id` ; `call_id` remis ⟹ le second le nomme.
"""
from __future__ import annotations

import pytest

from oto_mcp.capabilities._types import ResolvedCtx
from oto_mcp.capabilities.datastore import activity as dsa
from oto_mcp.db import usage


# Une ligne de `tool_calls` telle que la lit `_DS_ACTIVITY_SELECT`, sur le geste le
# plus RICHE : une transition de statut faite au cockpit, sous un run. Les gestes plus
# pauvres (un `data_rows` d'agent) ne portent qu'un sous-ensemble de valeurs — jamais
# de clé en plus, `_ds_activity_entry` posant les mêmes clés dans tous les cas.
LIGNE_SQL = {
    "created_at": "2026-09-01 10:00:00",
    "kind": "rest",
    "tool": "data_write",
    "args": {"namespace": "leads-clients", "ns_id": 160, "id": "row-1",
             "fields": ["statut"], "from_status": "enrichi", "to_status": "ecarte"},
    "ok": True,
    "error": None,
    "sub": "u-1",
    "email": "alexis@otomata.tech",
    "run_id": 7,
    "run_label": "Enrichissement",
    "doctrine": "enrichissement-lead",
    "outcome": "done",
}


def _servies() -> set[str]:
    return set(usage._ds_activity_entry(dict(LIGNE_SQL)))


def _declarees() -> set[str]:
    return set(dsa.ActivityEntry.model_fields)


def test_le_banc_voit_bien_une_entree_complete():
    """Témoin. Sans lui, un producteur qui rendrait `{}` mettrait les deux tests
    suivants au vert sans avoir rien comparé — le mode d'échec d'un banc qui mesure
    une différence d'ensembles."""
    servies = _servies()
    assert len(servies) >= 15, sorted(servies)
    # Les quatre que le cockpit consomme pour annuler : si elles disparaissent du
    # producteur, ce fichier doit rougir ICI et pas ailleurs.
    assert {"row_id", "fields", "from_status", "to_status"} <= servies


def test_toute_cle_servie_est_declaree():
    """Le sens qui compte le plus : un champ envoyé et absent du contrat est un champ
    qu'un client redemande, ou pire, qu'il découvre en l'observant et se met à lire
    sans qu'aucune promesse ne le couvre."""
    manquantes = _servies() - _declarees()
    assert not manquantes, (
        f"servies mais absentes d'ActivityEntry : {sorted(manquantes)}")


def test_aucun_champ_declare_ne_reste_jamais_servi():
    """L'autre sens, que le cliquet des connecteurs n'a pas besoin de tenir et que
    celui-ci doit : ici la promesse creuse a réellement existé pendant des mois, et
    elle se lit `undefined` côté client, jamais comme une erreur."""
    fantomes = _declarees() - _servies()
    assert not fantomes, (
        f"déclarées par ActivityEntry mais jamais servies : {sorted(fantomes)}")


def test_la_surface_n_ajoute_aucune_cle(monkeypatch):
    """Le contrat se juge sur ce qui SORT de la capacité, pas sur ce que le SQL rend :
    la surface enrichit chaque entrée (`row_title`, `email` résolu à la lecture). Une
    clé posée là échapperait aux deux tests ci-dessus."""
    class _Store:
        def get_row(self, namespace, row_id):
            return {"societe": "DEXXON GROUPE"}

        def declared_key(self, namespace):
            return "societe"

    monkeypatch.setattr(dsa, "make_store", lambda sub: _Store())
    monkeypatch.setattr(dsa.datastore_journal, "context",
                        lambda store, ns, **kw: type("C", (), {
                            "owner_type": "org", "owner_id": "2",
                            "title_key": "societe", "name": ns})())
    monkeypatch.setattr(dsa.db, "datastore_row_activity",
                        lambda *a, **kw: [usage._ds_activity_entry(dict(LIGNE_SQL))])
    monkeypatch.setattr(dsa.db, "emails_by_subs", lambda subs: {})

    out = dsa._row_activity(ResolvedCtx(sub="u-1"),
                            dsa.RowActivityInput(namespace="160", row_id="row-1"))

    servies = set(out["activity"][0])
    assert servies == _declarees(), {
        "en trop sur le fil": sorted(servies - _declarees()),
        "promises et absentes": sorted(_declarees() - servies),
    }
