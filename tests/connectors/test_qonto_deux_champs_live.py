"""`oto-mcp maintenance qonto-deux-champs` (#1906) : les clés `qonto` qui portent encore la
paire `login:clé` dans le chiffré passent au format deux champs — la clé seule au coffre,
le login dans `meta.login`.

À blanc par défaut, des comptes seulement ; `--appliquer` réécrit sous verrou, par
l'entonnoir du coffre, en gardant le reste de `meta`. Un `meta.login` qui diffère du
préfixe n'est jamais tranché en silence : compté à part, jamais converti. Aucune valeur,
aucun login, aucun identifiant de ligne dans la sortie. Vrai PostgreSQL : ce qu'on vérifie
est ce que le COFFRE porte.
"""
from __future__ import annotations

import json

import pytest

from oto_mcp import credentials_store as cs
from oto_mcp import crypto, maintenance
from oto_mcp.connectors import qonto_deux_champs as q

_KEY = "7" * 64

# Des valeurs reconnaissables, pour prouver qu'aucune ne sort.
SECRET = "SK-SECRET-A-NE-JAMAIS-IMPRIMER"
LOGIN = "login-exemple-1234"
AUTRE_LOGIN = "login-autre-9999"
ID_ORG = "987654"   # une org a un identifiant numérique
SUB = "sub-identifiant-a-taire"


def _exec(sql, params=()):
    from oto_mcp.db import _connect
    with _connect() as conn:
        return conn.execute(sql, params)


def _poser(entity_type: str, entity_id: str, clair: str, meta: dict | None = None,
           account: str = "", connector: str = "qonto") -> None:
    """Une ligne telle que le coffre la porte : le clair chiffré sous l'AAD de la ligne."""
    enc = crypto.encrypt(clair, cs._aad(entity_type, entity_id, connector, account))
    _exec("INSERT INTO connector_credentials (entity_type, entity_id, connector, account, "
          "secret_enc, secret_kind, meta, set_by) VALUES (%s, %s, %s, %s, %s, 'api_key', "
          "%s, 'poseur')",
          (entity_type, entity_id, connector, account, enc, json.dumps(meta or {})))


def _ligne(entity_type: str, entity_id: str, account: str = "",
           connector: str = "qonto") -> dict:
    r = dict(_exec("SELECT secret_enc, meta, version, set_by FROM connector_credentials "
                   "WHERE entity_type = %s AND entity_id = %s AND connector = %s "
                   "AND account = %s",
                   (entity_type, entity_id, connector, account)).fetchone())
    r["clair"] = crypto.decrypt(r["secret_enc"],
                                cs._aad(entity_type, entity_id, connector, account))
    return r


@pytest.fixture(autouse=True)
def coffre_vide(live, monkeypatch):
    monkeypatch.setenv("OTO_MCP_MASTER_KEY", _KEY)
    _exec("DELETE FROM connector_instances")
    _exec("DELETE FROM connector_credentials")


def _seme() -> None:
    """Un jeu mixte, toutes entités et comptes confondus."""
    # Déjà au format deux champs.
    _poser("org", "1", SECRET, {"login": LOGIN})
    # À convertir, meta.login absent (org, puis un second compte de la même org).
    _poser("org", ID_ORG, f"{LOGIN}:{SECRET}", {"verified_at": "2026-11-01", "x": 1})
    _poser("org", ID_ORG, f"{LOGIN}:{SECRET}", account="second")
    # À convertir, meta.login égal au préfixe (palier personnel).
    _poser("user", SUB, f" {LOGIN} : {SECRET}\n", {"login": LOGIN, "health_ko": False})
    # À convertir, meta.login DIFFÉRENT : refusée.
    _poser("member", f"1:{SUB}", f"{LOGIN}:{SECRET}", {"login": AUTRE_LOGIN})
    # Préfixe vide : refusée.
    _poser("org", "2", f":{SECRET}")
    # Indéchiffrable : chiffrée sous l'AAD d'une autre ligne.
    _exec("INSERT INTO connector_credentials (entity_type, entity_id, connector, account, "
          "secret_enc, secret_kind, meta) VALUES ('org', '3', 'qonto', '', %s, 'api_key', "
          "'{}')", (crypto.encrypt(f"{LOGIN}:{SECRET}", "une autre ligne"),))
    # Un autre connecteur, porteur d'un `:` : jamais lu.
    _poser("org", "1", f"{LOGIN}:{SECRET}", connector="serper")


def _comptes(rapport: q.Rapport) -> dict:
    return {"total": rapport.total, "a_convertir": rapport.a_convertir,
            "refusees": rapport.refusees, **rapport.comptes}


ATTENDU = {"total": 7, "a_convertir": 5, "refusees": 2,
           q.DEUX_CHAMPS: 1, q.LOGIN_ABSENT: 2, q.LOGIN_EGAL: 1,
           q.LOGIN_DIFFERENT: 1, q.PREFIXE_VIDE: 1, q.CLE_VIDE: 0,
           q.INDECHIFFRABLE: 1}


def test_a_blanc_compte_exactement_et_n_ecrit_rien(capsys):
    _seme()
    avant = _exec("SELECT entity_type, entity_id, connector, account, secret_enc, meta, "
                  "version, set_at FROM connector_credentials ORDER BY 1, 2, 3, 4"
                  ).fetchall()

    rapport = q.convertir(appliquer=False)

    assert _comptes(rapport) == ATTENDU
    assert rapport.converties == 0 and rapport.erreurs == 0
    apres = _exec("SELECT entity_type, entity_id, connector, account, secret_enc, meta, "
                  "version, set_at FROM connector_credentials ORDER BY 1, 2, 3, 4"
                  ).fetchall()
    assert apres == avant, "à blanc, rien n'est écrit"


def test_appliquer_convertit_et_garde_le_reste_du_meta():
    _seme()

    rapport = q.convertir(appliquer=True)

    assert _comptes(rapport) == ATTENDU, "les mêmes comptes qu'à blanc"
    assert rapport.converties == 3 and rapport.erreurs == 0

    org = _ligne("org", ID_ORG)
    assert org["clair"] == SECRET
    assert org["meta"] == {"verified_at": "2026-11-01", "x": 1, "login": LOGIN}
    assert org["set_by"] == "poseur", "qui a posé la clé reste celui qui l'a posée"
    assert org["version"] == 2

    second = _ligne("org", ID_ORG, account="second")
    assert second["clair"] == SECRET and second["meta"] == {"login": LOGIN}

    user = _ligne("user", SUB)
    assert user["clair"] == SECRET, "la clé seule, nettoyée"
    assert user["meta"] == {"login": LOGIN, "health_ko": False}

    assert _ligne("org", "1")["clair"] == SECRET, "déjà au format : intacte"
    assert _ligne("org", "1", connector="serper")["clair"] == f"{LOGIN}:{SECRET}"


def test_un_login_different_ou_un_prefixe_vide_ne_sont_jamais_convertis():
    _seme()
    avant_membre, avant_vide = _ligne("member", f"1:{SUB}"), _ligne("org", "2")

    q.convertir(appliquer=True)

    assert _ligne("member", f"1:{SUB}") == avant_membre
    assert _ligne("org", "2") == avant_vide


def test_rejouer_ne_trouve_plus_rien_a_convertir():
    _seme()
    q.convertir(appliquer=True)

    rapport = q.convertir(appliquer=True)

    assert rapport.converties == 0
    assert rapport.comptes[q.DEUX_CHAMPS] == 4
    assert rapport.comptes[q.LOGIN_ABSENT] == rapport.comptes[q.LOGIN_EGAL] == 0
    assert rapport.comptes[q.LOGIN_DIFFERENT] == rapport.comptes[q.PREFIXE_VIDE] == 1


def test_la_ligne_est_rejugee_sous_verrou(monkeypatch):
    """Changée entre la lecture et le verrou (un `meta.login` différent posé entre-temps) :
    rejugée, elle n'est plus convertie."""
    _poser("org", ID_ORG, f"{LOGIN}:{SECRET}")
    vrai = q._convertir

    def _intercale(cle_ligne, rang):
        _exec("UPDATE connector_credentials SET meta = %s WHERE connector = 'qonto'",
              (json.dumps({"login": AUTRE_LOGIN}),))
        return vrai(cle_ligne, rang)

    monkeypatch.setattr(q, "_convertir", _intercale)
    rapport = q.convertir(appliquer=True)

    assert rapport.converties == 0
    assert rapport.comptes[q.LOGIN_DIFFERENT] == 1
    assert _ligne("org", ID_ORG)["clair"] == f"{LOGIN}:{SECRET}"


def test_une_erreur_sur_une_ligne_est_comptee_et_les_autres_passent(monkeypatch, capsys,
                                                                    caplog):
    _poser("org", "11", f"{LOGIN}:{SECRET}")
    _poser("org", "12", f"{LOGIN}:{SECRET}")
    vrai = q._convertir

    def _premiere_en_erreur(cle_ligne, rang):
        if rang == 1:
            raise cs.ConcurrencyConflict(f"{cle_ligne} {SECRET}")
        return vrai(cle_ligne, rang)

    monkeypatch.setattr(q, "_convertir", _premiere_en_erreur)
    caplog.set_level("INFO")

    code = maintenance.main(["qonto-deux-champs", "--appliquer"])

    assert code == maintenance.QONTO_ERREUR
    assert _ligne("org", "11")["clair"] == f"{LOGIN}:{SECRET}"
    assert _ligne("org", "12")["clair"] == SECRET
    sortie = capsys.readouterr()
    assert "converties                        : 1" in sortie.out
    assert "erreurs                           : 1" in sortie.out
    tout = sortie.out + sortie.err + caplog.text
    assert SECRET not in tout and "987654" not in tout


def test_aucune_valeur_ni_identifiant_dans_la_sortie(capsys, caplog):
    _seme()
    caplog.set_level("INFO")

    assert maintenance.main(["qonto-deux-champs"]) == maintenance.QONTO_FAIT
    assert maintenance.main(["qonto-deux-champs", "--appliquer"]) == maintenance.QONTO_FAIT

    sortie = capsys.readouterr()
    assert "À BLANC" in sortie.out and "APPLIQUÉ" in sortie.out
    tout = sortie.out + sortie.err + caplog.text
    for valeur in (SECRET, LOGIN, AUTRE_LOGIN, ID_ORG, SUB):
        assert valeur not in tout, valeur


@pytest.mark.parametrize("clair, meta, verdict", [
    ("cle-seule", None, q.DEUX_CHAMPS),
    ("a:b:cle", None, q.LOGIN_ABSENT),
    ("log:cle", {"login": "  "}, q.LOGIN_ABSENT),
    ("log:cle", {"login": "log"}, q.LOGIN_EGAL),
    ("log:cle", {"login": "autre"}, q.LOGIN_DIFFERENT),
    (" :cle", {"login": "log"}, q.PREFIXE_VIDE),
    ("log: ", None, q.CLE_VIDE),
])
def test_juger(clair, meta, verdict):
    assert q.juger(clair, meta).verdict == verdict


def test_juger_prend_ce_qui_suit_le_dernier_deux_points():
    j = q.juger("a:b:cle", None)
    assert (j.login, j.cle) == ("a:b", "cle")
