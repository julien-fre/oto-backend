"""Revue de #1007 : ce qui aurait échoué contre la vraie API Yousign.

Chaque test porte un défaut relevé en revue. Le client est le double en mémoire de
`test_yousign_tools.py` (même fixture) : aucun appel réseau.

- **Champs de signature** : Yousign refuse d'activer un signataire sans champ. Les
  champs viennent des Smart Anchors du PDF — le document part avec
  `parse_anchors`, APRÈS les signataires (l'ancre `sN` vise le N-ième créé), et
  un PDF qui a moins d'ancres que de signataires est refusé, brouillon supprimé.
- **Hôte sandbox** : une clé du bac à sable n'est acceptée que par l'hôte sandbox.
- **Ordre** : « l'ordre de la liste est l'ordre de signature » n'est vrai que si
  `ordered_signers` part à la création.
- **Refus nommés** : base64 invalide, liste vide, fichier absent, refus amont 4xx.
"""
from __future__ import annotations

import base64

import pytest
from fastmcp.exceptions import ToolError

from oto_mcp import access

from test_yousign_tools import _Credential, _appeler, mcp  # noqa: E402,F401 — fixture

PDF = base64.b64encode(b"%PDF-1.7 {{s1|signature|180|60}}").decode()
ALICE = {"prenom": "Alice", "nom": "A", "email": "alice@example.com"}
BOB = {"prenom": "Bob", "nom": "B", "email": "bob@example.com"}


def _creer(mcp, signataires, **kw):
    return _appeler(mcp, "yousign_creer", nom="Accord", pdf_base64=kw.pop("pdf", PDF),
                    nom_fichier="a.pdf", signataires=signataires, **kw)


def _refus(mcp, outil, **arguments) -> str:
    with pytest.raises(ToolError) as e:
        _appeler(mcp, outil, **arguments)
    return str(e.value)


# --- champs de signature -----------------------------------------------------

def test_le_document_part_avec_les_ancres_analysees(mcp):
    _creer(mcp, [ALICE])
    documents = [kw for m, kw in mcp._faux.appels if m == "add_document"]
    assert documents == [{"parse_anchors": "true"}]


def test_les_signataires_sont_crees_AVANT_le_document(mcp):
    """L'ancre `sN` vise le N-ième signataire créé : un document posé avant ses
    signataires n'aurait aucune ancre rattachée."""
    _creer(mcp, [ALICE, BOB])
    ordre = [m for m, _ in mcp._faux.appels if m in ("add_signer", "add_document")]
    assert ordre == ["add_signer", "add_signer", "add_document"]


def test_un_pdf_sans_ancre_pour_chaque_signataire_est_refuse_et_le_brouillon_supprime(mcp):
    mcp._faux.ancres = 1
    msg = _refus(mcp, "yousign_creer", nom="Accord", pdf_base64=PDF, nom_fichier="a.pdf",
                 signataires=[ALICE, BOB])
    assert "1 ancre(s) de signature pour 2 signataire(s)" in msg
    assert "{{sN|signature|largeur|hauteur}}" in msg
    (demande_id,) = mcp._faux.demandes
    assert mcp._faux.supprimees == [demande_id]


def test_le_nombre_dancres_est_rendu(mcp):
    r = _creer(mcp, [ALICE])
    assert r["ancres"] == 1


# --- ordre de signature -------------------------------------------------------

def test_plusieurs_signataires_signent_dans_lordre_de_la_liste(mcp):
    r = _creer(mcp, [ALICE, BOB])
    (corps,) = [kw for m, kw in mcp._faux.appels if m == "create_signature_request"]
    assert corps == {"ordered_signers": True}
    signataires = [kw for m, kw in mcp._faux.appels if m == "add_signer"]
    assert signataires == [{}, {"insert_after_id": r["signataires"][0]["id"]}]


def test_un_seul_signataire_ne_pose_pas_dordre(mcp):
    _creer(mcp, [ALICE])
    (corps,) = [kw for m, kw in mcp._faux.appels if m == "create_signature_request"]
    assert corps == {}


# --- hôte sandbox ----------------------------------------------------------------

def test_une_cle_de_production_vise_lhote_de_production(mcp):
    _appeler(mcp, "yousign_statut", demande_id=_creer(mcp, [ALICE])["demande_id"])
    assert all(kw["sandbox"] is False for kw in mcp._construits)


def test_une_cle_sandbox_vise_lhote_sandbox(mcp, monkeypatch):
    monkeypatch.setattr(access, "resolve_credential",
                        lambda provider, want="auto", **kw:
                        _Credential(key="k-test", environment="sandbox"))
    _creer(mcp, [ALICE])
    assert mcp._construits and all(kw["sandbox"] is True for kw in mcp._construits)


def test_le_credential_se_resout_en_byo(mcp, monkeypatch):
    vus = []

    def _resoudre(provider, want="auto", **kw):
        vus.append((provider, want))
        return _Credential(key="k-test")

    monkeypatch.setattr(access, "resolve_credential", _resoudre)
    _creer(mcp, [ALICE])
    assert vus == [("yousign", "byo")]


# --- refus nommés ------------------------------------------------------------------

def test_une_liste_de_signataires_vide_est_refusee_sans_rien_creer(mcp):
    msg = _refus(mcp, "yousign_creer", nom="Accord", pdf_base64=PDF, nom_fichier="a.pdf",
                 signataires=[])
    assert "au moins un signataire" in msg
    assert mcp._faux.demandes == {}


def test_un_signataire_sans_email_est_refuse_sans_rien_creer(mcp):
    msg = _refus(mcp, "yousign_creer", nom="Accord", pdf_base64=PDF, nom_fichier="a.pdf",
                 signataires=[{"prenom": "A", "nom": "B"}])
    assert "`email`" in msg
    assert mcp._faux.demandes == {}


def test_un_base64_invalide_est_refuse_sans_rien_creer(mcp):
    msg = _refus(mcp, "yousign_creer", nom="Accord", pdf_base64="pas du base64 !",
                 nom_fichier="a.pdf", signataires=[ALICE])
    assert "base64 valide" in msg
    assert mcp._faux.demandes == {}


def test_un_echec_en_route_supprime_le_brouillon(mcp):
    from oto.tools.common import UpstreamHTTPError
    mcp._faux.leve_sur["add_signer"] = UpstreamHTTPError(
        400, {"detail": "invalid email"}, service="yousign")
    msg = _refus(mcp, "yousign_creer", nom="Accord", pdf_base64=PDF, nom_fichier="a.pdf",
                 signataires=[ALICE])
    assert "HTTP 400" in msg and "invalid email" in msg
    (demande_id,) = mcp._faux.demandes
    assert mcp._faux.supprimees == [demande_id]


def test_un_brouillon_impossible_a_supprimer_est_nomme(mcp):
    from oto.tools.common import UpstreamHTTPError
    mcp._faux.leve_sur["add_signer"] = UpstreamHTTPError(
        400, {"detail": "invalid email"}, service="yousign")
    mcp._faux.leve_sur["delete_signature_request"] = RuntimeError("réseau coupé")
    msg = _refus(mcp, "yousign_creer", nom="Accord", pdf_base64=PDF, nom_fichier="a.pdf",
                 signataires=[ALICE])
    (demande_id,) = mcp._faux.demandes
    assert f"le brouillon {demande_id} n'a pas pu être supprimé" in msg
    assert "à retirer à la main" in msg


def test_un_refus_de_cle_dit_quoi_faire(mcp):
    from oto.tools.common import UpstreamHTTPError
    mcp._faux.leve_sur["create_signature_request"] = UpstreamHTTPError(
        401, {"detail": "Unauthorized"}, service="yousign")
    msg = _refus(mcp, "yousign_creer", nom="Accord", pdf_base64=PDF, nom_fichier="a.pdf",
                 signataires=[ALICE])
    assert "refuse cette clé (401)" in msg
    assert "environnement" in msg


def test_un_fichier_absent_est_nomme(mcp):
    c = _creer(mcp, [ALICE])
    mcp._faux.telecharge = b""
    msg = _refus(mcp, "yousign_document_signe", demande_id=c["demande_id"],
                 document_id=c["document_id"])
    assert "aucun fichier" in msg


def test_un_5xx_amont_reste_ce_quil_est(mcp):
    """Un 5xx se réessaie : il ne devient pas un refus d'arguments."""
    from oto.tools.common import UpstreamHTTPError
    c = _creer(mcp, [ALICE])
    mcp._faux.leve_sur["activate_signature_request"] = UpstreamHTTPError(
        503, {"detail": "maintenance"}, service="yousign")
    orig = mcp._faux.activate_signature_request

    def _active(sr_id):
        mcp._faux._peut_lever("activate_signature_request")
        return orig(sr_id)

    mcp._faux.activate_signature_request = _active
    msg = _refus(mcp, "yousign_envoyer", demande_id=c["demande_id"])
    assert "refusé la requête" not in msg
