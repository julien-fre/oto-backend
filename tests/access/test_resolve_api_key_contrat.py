"""`resolve_api_key` est un seam bouchonné partout et appelé nulle part.

C'est la vue par laquelle les tools **keyed** obtiennent leur clé : 125 sites d'appel en
production. Dans la suite, elle est remplacée par une doublure **104 fois, dans 72
fichiers** — et jusqu'au 15/09/2026, aucun test ne l'exécutait. Éprouvé ce jour-là en
injectant la régression : on lui a fait rendre une clé VIDE, et les 1 763 tests dont le
nom porte « access », « credential » ou « key » sont restés verts. C'était la seule des
six régressions injectées dans le backend que la suite n'a pas vue.

La logique de cascade, elle, est tenue ailleurs (`oto_mcp/access/resolve.py`, 91 %, et
`tests/test_coffre_secret_illisible_bruyant.py` pour le refus d'un secret illisible). Ce
qui n'était tenu par rien, c'est le **contrat de la vue** : ce qu'elle demande à la
cascade, et la forme de ce qu'elle rend. Si l'un des deux bouge, les 104 doublures
continuent de rendre ce qu'elles ont toujours rendu — et la suite reste verte pendant que
la production sert autre chose.

D'où des doubles qui sont de VRAIS `ResolvedCredential` et non des `MagicMock` nus : sur
un mock nu, `rc.nimporte_quoi` répond, et une inversion du couple rendu passerait.
"""
from __future__ import annotations

import pytest
from mcp.types import ErrorData, INVALID_PARAMS

from oto_mcp.access import resolve, views
from oto_mcp.access.resolved_credential import ResolvedCredential
from oto_mcp.mcp_errors import McpError


def _credential(secret="sk-la-vraie-cle", is_platform=False, mode="user"):
    return ResolvedCredential(provider="apollo", secret=secret,
                              is_platform=is_platform, mode=mode)


@pytest.fixture
def appels(monkeypatch):
    """Remplace la CASCADE, jamais la vue : c'est la vue qu'on éprouve ici."""
    vus = []

    def _faux(provider, want="auto", sub=None, *, account=None, **kw):
        vus.append({"provider": provider, "want": want, "account": account, **kw})
        return _credential()

    monkeypatch.setattr(resolve, "resolve_credential", _faux)
    return vus


# ── ce que la vue DEMANDE à la cascade ───────────────────────────────────────

def test_la_cle_est_demandee_en_auto_donc_le_palier_plateforme_reste_eligible(appels):
    views.resolve_api_key("apollo")
    assert appels[0]["want"] == "auto", (
        "en `byo`, les tools keyed perdraient la clé PLATEFORME sans que rien ne le dise — "
        "c'est ce qui distingue cette vue de `resolve_credential_fields`")


def test_le_compte_choisi_est_transmis_a_la_cascade(appels):
    views.resolve_api_key("apollo", account="le-second-compte")
    assert appels[0]["account"] == "le-second-compte", (
        "non transmis, un connecteur multi-compte résoudrait toujours le même")


def test_sans_compte_explicite_la_cascade_choisit(appels):
    views.resolve_api_key("apollo")
    assert appels[0]["account"] is None


def test_le_connecteur_demande_est_celui_qu_on_passe(appels):
    views.resolve_api_key("pennylane")
    assert appels[0]["provider"] == "pennylane"


# ── la forme de ce qu'elle REND ──────────────────────────────────────────────

def test_elle_rend_la_cle_puis_son_origine_dans_cet_ordre(monkeypatch):
    # Inverser le couple donnerait un BOOLÉEN comme clé d'API : le client partirait
    # s'authentifier avec `False`, et l'erreur remonterait du fournisseur, pas d'ici.
    monkeypatch.setattr(resolve, "resolve_credential",
                        lambda *a, **k: _credential(secret="sk-42", is_platform=True))
    cle, plateforme = views.resolve_api_key("apollo")
    assert cle == "sk-42"
    assert plateforme is True


def test_la_cle_rendue_est_le_secret_du_credential_gagnant(monkeypatch):
    monkeypatch.setattr(resolve, "resolve_credential",
                        lambda *a, **k: _credential(secret="sk-du-gagnant"))
    assert views.resolve_api_key("apollo")[0] == "sk-du-gagnant"


def test_une_cle_byo_n_est_pas_annoncee_comme_plateforme(monkeypatch):
    # `is_platform` commande le décompte de quota côté appelant : le dire à tort
    # ferait débiter un siège plateforme pour une clé que l'org a apportée.
    monkeypatch.setattr(resolve, "resolve_credential",
                        lambda *a, **k: _credential(is_platform=False, mode="org"))
    assert views.resolve_api_key("apollo")[1] is False


def test_la_cle_servie_n_est_jamais_vide(monkeypatch):
    """La régression qui était passée : une clé vide servie comme si de rien n'était.

    La garde vit dans la cascade (`resolve.py`) ; cette assertion la tient AUSSI au
    point de sortie, là où les 125 appelants lisent. Un client instancié avec une chaîne
    vide part chez le fournisseur et revient en 401 — l'erreur accuse alors le
    fournisseur, jamais le coffre.
    """
    monkeypatch.setattr(resolve, "resolve_credential", lambda *a, **k: _credential())
    cle, _ = views.resolve_api_key("apollo")
    assert cle, "une clé vide ne doit jamais sortir de cette vue"


# ── ce qu'elle ne rattrape PAS ───────────────────────────────────────────────

def test_un_refus_de_la_cascade_remonte_tel_quel(monkeypatch):
    # La vue ne doit ni avaler la McpError ni la traduire en `("", False)` : l'appelant
    # a besoin du message actionnable (credential absent / quota dépassé / RBAC refusé).
    def _refuse(*a, **k):
        raise McpError(ErrorData(code=INVALID_PARAMS, message="aucun credential apollo"))

    monkeypatch.setattr(resolve, "resolve_credential", _refuse)
    with pytest.raises(McpError, match="aucun credential apollo"):
        views.resolve_api_key("apollo")
