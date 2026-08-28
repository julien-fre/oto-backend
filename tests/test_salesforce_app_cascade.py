"""L'application est une infrastructure d'org ; le jeton est une identité.

Avant, les deux se lisaient au MÊME endroit : pour connecter son propre Salesforce, un
membre devait recoller le Consumer Key, le Consumer Secret et la Login URL de
l'application de son org — c'est-à-dire connaître un secret qui ne le regarde pas, et
que l'admin devait lui transmettre à la main.

Désormais :
- **lecture** de l'application EN CASCADE, du scope demandé vers le haut ;
- **écriture** du jeton au scope demandé, exactement.

Cette asymétrie est le point : l'admin pose l'application une fois au niveau org, chaque
membre n'a plus qu'à consentir avec son propre compte Salesforce.
"""
from __future__ import annotations

import pytest

from oto_mcp import credentials_store
from oto_mcp.auth import salesforce as so

APP = {"client_id": "ci-org", "client_secret": "cs-org",
       "login_url": "https://x.my.salesforce.com"}


@pytest.fixture
def coffre(monkeypatch):
    store: dict[tuple, dict] = {}
    monkeypatch.setattr(credentials_store, "get_credential_with_meta",
                        lambda et, eid, c, account="": (
                            {"secret": store[(et, eid)]} if (et, eid) in store else None))
    monkeypatch.setattr(credentials_store, "unpack_secret", lambda _c, s: dict(s))
    return store


# --- la cascade de lecture -----------------------------------------------------

def test_un_membre_utilise_lapplication_de_son_org(coffre):
    """LE cas qui motive tout : l'org pose l'app, le membre n'a rien à coller."""
    coffre[("org", "2")] = dict(APP)
    trouve = so._read_app(2, "sub-x", "member", None)
    assert trouve == APP


def test_lapplication_du_membre_prime_sur_celle_de_lorg(coffre):
    """Un membre qui a la sienne (autre org Salesforce, sandbox…) la garde."""
    coffre[("org", "2")] = dict(APP)
    coffre[(credentials_store.MEMBER, credentials_store.member_id(2, "sub-x"))] = {
        **APP, "client_id": "ci-perso"}
    assert so._read_app(2, "sub-x", "member", None)["client_id"] == "ci-perso"


def test_lequipe_sintercale_entre_le_membre_et_lorg(coffre):
    coffre[("org", "2")] = dict(APP)
    coffre[("group", "7")] = {**APP, "client_id": "ci-equipe"}
    assert so._read_app(2, "sub-x", "member", 7)["client_id"] == "ci-equipe"


def test_consentir_pour_lorg_nutilise_jamais_lapplication_dun_membre(coffre):
    """INVARIANT. La cascade REMONTE, elle ne descend pas : ranger un jeton d'org
    obtenu avec les identifiants d'un particulier lierait la connexion de toute
    l'org à l'application d'une personne."""
    coffre[(credentials_store.MEMBER, credentials_store.member_id(2, "sub-x"))] = dict(APP)
    assert so._read_app(2, "sub-x", "org", None) is None


def test_une_application_incomplete_est_ignoree_et_on_remonte(coffre):
    """Une ligne membre qui ne porte QUE le jeton (cas nominal après consentement en
    cascade) ne doit pas masquer l'application de l'org."""
    coffre[(credentials_store.MEMBER, credentials_store.member_id(2, "sub-x"))] = {
        "refresh_token": "RT-1"}
    coffre[("org", "2")] = dict(APP)
    assert so._read_app(2, "sub-x", "member", None) == APP


def test_aucune_application_nulle_part(coffre):
    assert so._read_app(2, "sub-x", "member", None) is None


# --- aller et retour doivent s'accorder ----------------------------------------

def test_le_callback_lit_la_meme_application_que_le_depart(coffre):
    """Le code d'autorisation est émis POUR un client_id précis : l'échanger avec un
    autre échoue. Si le départ prend l'app de l'org et le retour celle du membre (ou
    rien), la connexion casse au dernier moment — après le consentement de
    l'utilisateur, au pire endroit possible."""
    coffre[("org", "2")] = dict(APP)
    assert so.read_saved_fields("sub-x", 2, "member", None) == APP


def test_le_callback_prefere_la_ligne_du_scope_quand_elle_est_complete(coffre):
    coffre[("org", "2")] = dict(APP)
    coffre[(credentials_store.MEMBER, credentials_store.member_id(2, "sub-x"))] = {
        **APP, "client_id": "ci-perso", "refresh_token": "RT-1"}
    assert so.read_saved_fields("sub-x", 2, "member", None)["client_id"] == "ci-perso"


# --- l'écriture reste au scope demandé -----------------------------------------

def test_lecriture_vise_le_scope_pas_la_source_de_lapplication():
    """L'asymétrie, littéralement : on LIT en remontant, on ÉCRIT là où on a demandé."""
    assert so._fields_entity(2, "sub-x", "member") == (
        credentials_store.MEMBER, credentials_store.member_id(2, "sub-x"))
    assert so._fields_entity(2, "sub-x", "org") == ("org", "2")
    assert so._fields_entity(2, "sub-x", "group", 7) == ("group", "7")


def test_sans_equipe_la_cascade_reste_valide(coffre):
    """RÉGRESSION. Une première version calculait le point de départ par un index en
    supposant l'équipe toujours présente : sans elle, `niveaux[2:]` était vide et
    `_read_app` rendait None sur un cas parfaitement valide — « aucune application »
    alors que celle de l'org était là."""
    coffre[("org", "2")] = dict(APP)
    assert so._read_app(2, "sub-x", "org", None) == APP
    assert so._entites_montantes(2, "sub-x", "org", None) == [("org", "2")]


def test_les_entites_visitees_sont_celles_quon_croit():
    m = credentials_store.member_id(2, "sub-x")
    assert so._entites_montantes(2, "sub-x", "member", 7) == [
        (credentials_store.MEMBER, m), ("group", "7"), ("org", "2")]
    assert so._entites_montantes(2, "sub-x", "member", None) == [
        (credentials_store.MEMBER, m), ("org", "2")]
    assert so._entites_montantes(2, "sub-x", "group", 7) == [("group", "7"), ("org", "2")]
