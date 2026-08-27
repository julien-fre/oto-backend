"""Un résultat d'outil VIDE se sert en PHRASE, jamais en structure nue (oto#32).

Le 2026-08-27, une flotte d'agents a perdu la moitié de ses départs sur un
`{"total_count": 0, "rows": []}` rendu tel quel dans le canal texte : le décodage
du modèle dégénère dessus — il recopie la structure, boucle sur des centaines de
`]}`, reprend en prose, et le fournisseur encadre toute la sortie comme un appel
d'outil dont le nom est la narration. 16 des 26 faux départs d'une campagne, 10 des
11 d'une vague de production.

Le banc fait traverser à un résultat la chaîne RÉELLE montée sur `server.mcp` (les
instances de middleware du vrai serveur, dans leur vrai ordre) et lit ce qui sort
côté client : l'ordre des middlewares est ici la moitié du correctif, un banc qui
n'appellerait que la fonction de rendu ne prouverait donc rien. Aucune base n'est
requise — le journal d'appels est best-effort et se contente de râler.
"""
from __future__ import annotations

import asyncio

import pytest
from fastmcp import Client, FastMCP
from oto.tools.common import FieldFilter

from oto_mcp import redaction, server, session_org

# La forme EXACTE capturée en production, octet pour octet.
VIDE_CAPTURE = {"total_count": 0, "rows": []}


def _banc(fn, *, nom: str = "recherche"):
    """Un serveur d'un seul outil, sous la chaîne de middlewares du VRAI serveur."""
    m = FastMCP("banc")
    for mw in server.mcp.middleware:
        m.add_middleware(mw)
    m.tool(name=nom)(fn)
    return m


def _servir(m: FastMCP, nom: str = "recherche"):
    """Ce que le CLIENT reçoit : (texte servi, canal structuré)."""
    async def appel():
        async with Client(m) as c:
            r = await c.call_tool(nom, {})
            return "".join(getattr(b, "text", "") for b in r.content), r.structured_content
    return asyncio.run(appel())


def _outil_vide(payload):
    def recherche() -> dict:
        return payload
    return recherche


# --- Le canal texte : la phrase, et rien d'autre ----------------------------

def test_le_dict_vide_capture_ne_part_jamais_en_structure():
    """RED avant le correctif : le texte servi était `{"total_count":0,"rows":[]}`."""
    texte, structure = _servir(_banc(_outil_vide(VIDE_CAPTURE)))
    assert texte == redaction.EMPTY_MESSAGE_DEFAULT
    assert "[]" not in texte and "{}" not in texte
    assert "[" not in texte and "{" not in texte
    # Le canal structuré, lui, porte toujours la structure vide : c'est la structure
    # DANS LE TEXTE qui déclenche la dégénérescence, pas son existence.
    assert structure == VIDE_CAPTURE


def test_la_liste_vide_ne_part_jamais_en_structure(monkeypatch):
    """La liste vide n'atteint le canal texte qu'en repassant par `rebuild_result`
    (rédaction, écho de compte) — sans quoi fastmcp ne sérialise rien. On l'y met
    donc : une policy de rédaction active, et le texte servi valait `[]`."""
    monkeypatch.setattr(redaction, "_resolve_field_filter",
                        lambda _s: FieldFilter(rules={"secret": "drop"}))

    def recherche() -> list:
        return []

    texte, _ = _servir(_banc(recherche))
    assert texte == redaction.EMPTY_MESSAGE_DEFAULT
    assert "[]" not in texte


def test_un_resultat_non_vide_est_rendu_tel_quel():
    plein = {"total_count": 1, "rows": [{"id": 1}]}
    texte, structure = _servir(_banc(_outil_vide(plein)))
    assert '"id":1' in texte.replace(" ", "")
    assert structure == plein


def test_le_gabarit_declare_par_l_outil_est_servi():
    texte, _ = _servir(_banc(_outil_vide({"results": [], "total_count": 0}),
                             nom="fr_accords_search"), nom="fr_accords_search")
    assert texte == "Aucun accord déposé pour ce SIREN."
    assert texte == redaction.EMPTY_MESSAGES["fr_accords_search"]


def test_l_echo_de_compte_ne_retablit_pas_la_structure():
    """L'écho de compte réémet le payload en JSON dans le canal texte. Il est plus
    interne que le rendu du vide — s'il tournait après, il rétablirait très
    exactement la structure qu'on vient d'en retirer."""
    def recherche() -> dict:
        # Ce que fait un vrai connecteur quand il a résolu un compte NOMMÉ.
        session_org.note_call_trace(resolved_connector="fr", resolved_account="client-x")
        return dict(VIDE_CAPTURE)

    texte, structure = _servir(_banc(recherche, nom="fr_search"), nom="fr_search")
    assert texte == redaction.EMPTY_MESSAGE_DEFAULT
    assert "[]" not in texte
    # L'écho reste lisible là où il ne nuit pas : le canal structuré.
    assert structure.get("_account") == "client-x"


def test_une_erreur_n_est_pas_reecrite():
    def recherche() -> dict:
        raise ValueError("boum")

    m = _banc(recherche)

    async def appel():
        async with Client(m) as c:
            return await c.call_tool("recherche", {}, raise_on_error=False)

    r = asyncio.run(appel())
    assert r.is_error
    assert redaction.EMPTY_MESSAGE_DEFAULT not in "".join(
        getattr(b, "text", "") for b in r.content)


# --- La règle de détection, isolée -----------------------------------------

@pytest.mark.parametrize("payload", [
    [],
    VIDE_CAPTURE,
    {"rows": []},
    {"results": [], "total_count": 0},
    {"result": []},                       # l'enveloppe fastmcp d'un retour `list`
    {"items": [], "hits": [], "count": 0},
    {"rows": [], "_account": "client-x"},  # un scalaire à côté ne réveille rien
    {"rows": [], "next_cursor": None},
])
def test_est_vide(payload):
    assert redaction.is_empty_payload(payload) is True


@pytest.mark.parametrize("payload", [
    None,
    "",
    0,
    {},                                    # aucune collection : rien à dire du vide
    {"ok": True},
    {"rows": [{"id": 1}]},
    {"rows": [], "total_count": 3},        # un compteur non nul CONTREDIT la collection
    {"rows": [], "items": [{"id": 1}]},    # une seule collection peuplée suffit
    {"data": {"rows": []}},                # le vide ne se cherche qu'à la racine
])
def test_n_est_pas_vide(payload):
    assert redaction.is_empty_payload(payload) is False


def test_le_compteur_booleen_n_est_pas_un_compteur():
    """`True` est un `int` en Python — un drapeau nommé `count` ne doit pas se lire
    comme un volume."""
    assert redaction.is_empty_payload({"rows": [], "count": True}) is True


# --- La garde générique : AUCUN outil monté n'échappe à la règle ------------

def _outils_montes() -> list[str]:
    """Les outils du VRAI montage (connecteurs + capacités), pas une liste écrite
    à la main : c'est ce qui fait qu'un outil ajouté demain est couvert d'office."""
    return [t.name for t in asyncio.run(server.mcp.list_tools(run_middleware=False))]


def test_aucun_outil_monte_ne_rend_une_structure_pour_un_vide():
    noms = _outils_montes()
    # Garde-fou de l'instrument : un registre vide ferait passer ce test à vide.
    assert len(noms) > 300, f"registre d'outils suspect ({len(noms)}) — banc invalide"
    fautifs = [n for n in noms
               if any(c in redaction.empty_message(n) for c in "[]{}")]
    assert not fautifs, (
        f"Gabarit de vide porteur d'une structure : {fautifs}. La phrase servie pour "
        "un résultat vide ne doit contenir ni crochet ni accolade — c'est très "
        "exactement le déclencheur qu'on retire.")


def test_chaque_outil_monte_sert_une_phrase_sur_un_vide():
    """La règle est GÉNÉRIQUE : elle se juge sur la forme du résultat, jamais sur le
    nom de l'outil. On le prouve sur tout le montage plutôt que sur un échantillon."""
    for nom in _outils_montes():
        phrase = redaction.empty_message(nom)
        rendu = redaction.render_empty(_ResultatFactice(VIDE_CAPTURE), nom)
        texte = "".join(getattr(b, "text", "") for b in rendu.content)
        assert texte == phrase, nom
        assert not any(c in texte for c in "[]{}"), nom
        assert rendu.structured_content == VIDE_CAPTURE, nom


class _ResultatFactice:
    """Ce que rend un tool FastMCP, réduit aux deux canaux qui portent la donnée."""

    def __init__(self, payload):
        self.structured_content = payload
        self.content = []
        self.is_error = False
