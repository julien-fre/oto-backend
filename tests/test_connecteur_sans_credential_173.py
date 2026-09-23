"""Un connecteur SANS credential n'a pas de couche « clé » (otomata-tech/oto#173).

Le fait : `droit`, `web`, `culture`, `foncier`… (`secret_kind="none"`) sortaient de
`oto_connector op=list` en `ready:false`, `not_ready:"no_credential"`, avec un geste
qui envoyait poser une clé qui n'existe pas — alors que leur fiche dit `auth: none`.
La marche de cascade n'a rien à résoudre pour eux : elle rend `forbidden` par
construction, et `diagnose` le lisait comme une clé absente.

Un banc PAR connecteur de la famille, dérivé du registre : un connecteur sans
credential ajouté demain est couvert sans qu'on pense à l'inscrire ici."""
from __future__ import annotations

import pytest

from oto_mcp import access, providers, status_hints
from oto_mcp.connectors import readiness

SANS_CREDENTIAL = sorted(
    c.name for c in providers.REGISTRY.values()
    if providers.credential_provider(c.name) not in providers.CREDENTIAL_PROVIDERS)


@pytest.fixture
def couches(monkeypatch):
    """Ce que la cascade réelle rend pour un porteur sans clé : `forbidden`, et
    aucun rejet — l'état exact de la carte du signalement."""
    monkeypatch.setattr(access, "paid_option_for", lambda c: None)
    monkeypatch.setattr(access, "credential_mode_for",
                        lambda sub, c, org=None, group=None: "forbidden")
    monkeypatch.setattr(access, "credential_rejection_for",
                        lambda sub, c, org=None, group=None: None)
    monkeypatch.setattr(status_hints, "pending_action",
                        lambda c, sub, org, group, st: None)


def test_la_famille_existe():
    """Le banc paramétré ne doit pas passer à vide."""
    assert {"droit", "web", "culture", "foncier", "osm", "urba"} <= set(SANS_CREDENTIAL)


@pytest.mark.parametrize("connecteur", SANS_CREDENTIAL)
def test_un_connecteur_sans_credential_est_pret(couches, connecteur):
    diag = readiness.diagnose("u1", connecteur, org=7, group=None)
    assert diag is None, (
        f"`{connecteur}` n'a aucun credential et reçoit {diag!r} — la carte "
        f"l'envoie poser une clé qui n'existe pas")


def test_un_geste_en_attente_reste_relaye(couches, monkeypatch):
    """Sauter la couche 2 ne saute pas l'étape restante : le hook du connecteur est
    toujours relayé, avec `mode=None` (aucune clé en jeu)."""
    vus = []
    monkeypatch.setattr(status_hints, "pending_action",
                        lambda c, sub, org, group, st: vus.append(st) or "lie un compte")
    diag = readiness.diagnose("u1", SANS_CREDENTIAL[0], org=7, group=None)
    assert diag == readiness.Diagnosis(readiness.PENDING_STEP, "lie un compte")
    assert vus == [{"mode": None}]


def test_un_connecteur_a_cle_reste_no_credential(couches):
    """Le contrefactuel : un porteur qui DÉTIENT un credential et n'en résout aucun
    reste `no_credential` — sinon le banc ci-dessus passerait pour une autre raison."""
    diag = readiness.diagnose("u1", "serper", org=7, group=None)
    assert diag is not None and diag.reason == readiness.NO_CREDENTIAL
