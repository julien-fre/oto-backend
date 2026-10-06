"""`declaration.py verifier` et le relais d'autorisation (#1164).

Un rôle dont le MCP sert la façade OAuth devant NOTRE annuaire administrable porte
l'hôte de son URL publique dans `OTO_MCP_OAUTH_RELAY_HOSTS`. Sans lui, le relais est
éteint sur cet hôte : les clients qui ont lu la métadonnée du relais voient leurs
rafraîchissements refusés — vécu une vingtaine de minutes à la mise en service d'une
instance dédiée, que `verifier` avait laissée passer.

La règle ne recopie pas le serveur à la main : ses constantes, sa normalisation et sa
condition sont confrontées ici au code qui consulte la liste (`oto_mcp/auth/relay.py`,
`oto_mcp/auth/facade.py`)."""
from __future__ import annotations

import copy
import importlib.util
import json
from urllib.parse import urlparse

import pytest

from _banc_cible import DECLARATION, DEPOT

_spec = importlib.util.spec_from_file_location(
    "declaration_cible", DEPOT / "deploy" / "cible" / "declaration.py")
decl = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(decl)

HOTE = "mcp.example.org"
LISTE = "OTO_MCP_OAUTH_RELAY_HOSTS"


@pytest.fixture
def doc():
    """Une déclaration synthétique : `prod` sert la façade devant un annuaire
    administrable (relayé) ; `preprod` n'en déclare pas le credential (non relayé)."""
    d = json.loads(DECLARATION.read_text())
    prod, preprod = d["roles"]["prod"], d["roles"]["preprod"]
    prod["hote_public"] = HOTE
    prod["env"].update(OTO_MCP_PUBLIC_URL=f"https://{HOTE}",
                       MCP_AUDIENCE=f"https://{HOTE}/mcp", **{LISTE: HOTE})
    assert prod["env"]["OTO_MCP_LOGTO_M2M_ID"] and prod["env"]["OTO_MCP_CLAUDE_APP_ID"]
    assert "OTO_MCP_LOGTO_M2M_SECRET" in prod["secrets_optionnels"]
    preprod["hote_public"] = "mcp-preprod.example.org"
    preprod["env"]["OTO_MCP_PUBLIC_URL"] = "https://mcp-preprod.example.org"
    assert "OTO_MCP_LOGTO_M2M_ID" not in preprod["env"]
    assert LISTE not in preprod["env"]
    return d


def _ecarts(doc) -> list[str]:
    try:
        decl.valider(copy.deepcopy(doc))
    except decl.Refus as refus:
        return refus.ecarts
    return []


def _ecarts_relais(doc) -> list[str]:
    return [e for e in _ecarts(doc) if LISTE in e or "OTO_MCP_PUBLIC_URL" in e]


def test_role_relaye_avec_son_hote_passe(doc):
    assert _ecarts(doc) == []


@pytest.mark.parametrize("liste", [None, "", "autre.example.org", "autre.example.org, "])
def test_role_relaye_sans_son_hote_est_refuse_en_le_nommant(doc, liste):
    env = doc["roles"]["prod"]["env"]
    if liste is None:
        del env[LISTE]
    else:
        env[LISTE] = liste
    [ecart] = _ecarts(doc)
    assert ecart.startswith(f"roles.prod.env.{LISTE} :")   # la variable, sous le rôle
    assert "rôle prod" in ecart and f"« {HOTE} »" in ecart  # le rôle, l'hôte manquant
    assert "rafraîchissements" in ecart


@pytest.mark.parametrize("liste", [
    f"autre.example.org,{HOTE}",
    f" {HOTE.upper()} ",
    f"{HOTE}.",
    f"autre.example.org , Mcp.Example.Org.",
])
def test_la_liste_se_lit_comme_le_serveur_la_lit(doc, liste):
    doc["roles"]["prod"]["env"][LISTE] = liste
    assert _ecarts_relais(doc) == []


@pytest.mark.parametrize("liste", [f"https://{HOTE}", f"{HOTE}:443", f"{HOTE}/",
                                   f"x{HOTE}", f"{HOTE}.org"])
def test_aucune_correspondance_approximative(doc, liste):
    """Le serveur compare des noms d'hôte nus : un schéma ou un port n'y correspond à
    rien, ni un hôte voisin."""
    doc["roles"]["prod"]["env"][LISTE] = liste
    assert len(_ecarts_relais(doc)) == 1


@pytest.mark.parametrize("url", [f"https://{HOTE.upper()}/", f"https://{HOTE}:8443",
                                 f"https://{HOTE}./"])
def test_l_hote_cherche_est_celui_de_l_url_publique_normalise(doc, url):
    doc["roles"]["prod"]["env"]["OTO_MCP_PUBLIC_URL"] = url
    assert _ecarts_relais(doc) == []


@pytest.mark.parametrize("url", ["", "pas-une-url", "https://[::1"])
def test_une_url_publique_sans_hote_est_nommee(doc, url):
    doc["roles"]["prod"]["env"]["OTO_MCP_PUBLIC_URL"] = url
    [ecart] = _ecarts_relais(doc)
    assert ecart.startswith("roles.prod.env.OTO_MCP_PUBLIC_URL : aucun nom d'hôte")


@pytest.mark.parametrize("modifier", [
    lambda r: r["env"].pop("OTO_MCP_LOGTO_M2M_ID"),
    lambda r: r["env"].update(OTO_MCP_LOGTO_M2M_ID=""),
    lambda r: r["secrets_optionnels"].remove("OTO_MCP_LOGTO_M2M_SECRET"),
])
def test_un_role_qui_ne_relaie_pas_n_est_pas_concerne(doc, modifier):
    r = doc["roles"]["prod"]
    modifier(r)
    del r["env"][LISTE]
    assert _ecarts(doc) == []
    # le rôle `preprod` de la déclaration, sans credential ni liste, passe aussi
    assert decl.hote_a_relayer(doc["roles"]["preprod"]["env"],
                               doc["roles"]["preprod"]["secrets_optionnels"]) is None


def test_sans_facade_la_regle_se_tait_et_la_sante_parle(doc):
    r = doc["roles"]["prod"]
    del r["env"]["OTO_MCP_CLAUDE_APP_ID"]
    del r["env"][LISTE]
    ecarts = _ecarts(doc)
    assert [e.split(" :")[0] for e in ecarts] == ["roles.prod.env.OTO_MCP_CLAUDE_APP_ID"]


def test_la_ligne_de_commande_refuse(doc, tmp_path, capsys):
    del doc["roles"]["prod"]["env"][LISTE]
    chemin = tmp_path / "d.json"
    chemin.write_text(json.dumps(doc))
    assert decl.main(["d", "verifier", str(chemin)]) == 1
    err = capsys.readouterr().err
    assert "déclaration REFUSÉE" in err and f"roles.prod.env.{LISTE}" in err and HOTE in err


# --- la règle dit ce que fait le serveur ----------------------------------------------
def test_les_constantes_sont_celles_du_serveur():
    from oto_mcp.auth import facade, relay
    assert decl.LISTE_DU_RELAIS == relay.HOSTS_ENV
    assert decl.CREDENTIAL_DE_L_ANNUAIRE == facade._PRIMARY_CREDENTIAL
    assert decl.INTERRUPTEUR_DE_LA_FACADE == "OTO_MCP_CLAUDE_APP_ID"
    serveur = (DEPOT / "oto_mcp/server.py").read_text(encoding="utf-8")
    # la façade reçoit l'URL publique déclarée : son hôte est celui qu'elle cherche
    assert f'oauth_facade.make_routes(\n                    require_env("{decl.URL_PUBLIQUE}")' \
        in serveur
    source = (DEPOT / "oto_mcp/auth/facade.py").read_text(encoding="utf-8")
    assert 'public_host = (urlparse(public_url).hostname or "").lower()' in source
    assert "relais=relay.relais_actif(public_host)" in source


@pytest.mark.parametrize("liste", ["", HOTE, f" {HOTE.upper()}. ", f"https://{HOTE}",
                                   f"{HOTE}:443", f"a.example.org,{HOTE}", "a.example.org"])
@pytest.mark.parametrize("url", [f"https://{HOTE}", f"https://{HOTE.upper()}:8443/",
                                 f"https://{HOTE}./"])
def test_meme_verdict_que_le_relais(monkeypatch, liste, url):
    from oto_mcp.auth import relay
    monkeypatch.setenv(relay.HOSTS_ENV, liste)
    monkeypatch.setenv("OTO_MCP_OAUTH_STATE_SECRET", "s" * 32)
    serveur = relay.relais_actif((urlparse(url.rstrip("/")).hostname or "").lower())
    env = {"OTO_MCP_CLAUDE_APP_ID": "app", "OTO_MCP_LOGTO_M2M_ID": "m2m",
           "OTO_MCP_PUBLIC_URL": url, LISTE: liste}
    hote = decl.hote_a_relayer(env, ["OTO_MCP_LOGTO_M2M_SECRET"])
    assert (hote in decl.hotes_du_relais(liste)) is serveur
    assert decl.hotes_du_relais(liste) == relay.hosts_declares()


def test_meme_condition_que_l_annuaire_administrable(monkeypatch):
    """Le relais n'agit que si NOTRE annuaire est administrable (`Cible.directory`) :
    exactement quand la règle exige l'hôte."""
    from oto_mcp.auth import facade, relay
    monkeypatch.setenv("LOGTO_ENDPOINT", "https://auth.example.org")
    monkeypatch.setenv("OTO_TENANT_PRIMAIRE_SLUG", "exemple")
    monkeypatch.setattr(facade, "tenant_for_host", lambda host: None)
    for id_, secret, exige in (("m2m", "s", True), ("", "s", False), ("m2m", "", False)):
        for n, v in (("OTO_MCP_LOGTO_M2M_ID", id_), ("OTO_MCP_LOGTO_M2M_SECRET", secret)):
            if v:
                monkeypatch.setenv(n, v)
            else:
                monkeypatch.delenv(n, raising=False)
        cible = relay.cible_pour_host(HOTE, f"https://{HOTE}", "app")
        env = {"OTO_MCP_CLAUDE_APP_ID": "app", "OTO_MCP_PUBLIC_URL": f"https://{HOTE}",
               "OTO_MCP_LOGTO_M2M_ID": id_}
        optionnels = ["OTO_MCP_LOGTO_M2M_SECRET"] if secret else []
        assert (cible.directory is not None) is exige
        assert (decl.hote_a_relayer(env, optionnels) == cible.host) is exige
