"""La doc connecteur vit dans des markdown, et doit rester servable.

Elle était un dict de 850 lignes de chaînes Python. Écrire de la prose là-dedans
décourage de la tenir à jour — et ça s'est vu : la doc Salesforce décrivait encore un
modèle d'application que Salesforce a désactivé depuis, et n'a jamais mentionné les
trois prérequis qui ont réellement bloqué une installation client (portées, relaxe
d'IP, rotation des jetons).

Le support a changé, pas le contrat. Ces tests figent ce qui doit rester vrai — un
fichier mal nommé ou un titre mal formé rendrait la doc silencieusement invisible,
ce qui est pire qu'une doc absente : personne ne s'en aperçoit.
"""
from __future__ import annotations

import pathlib

import pytest

from oto_mcp import connector_docs, providers

_DIR = pathlib.Path(connector_docs.__file__).parent / "connector_docs"


def test_les_fichiers_sont_tous_lus():
    """Un fichier présent mais non chargé = doc écrite pour rien. Cause probable :
    aucun titre `## kind — titre` reconnu, donc zéro section extraite."""
    sur_disque = {f.stem for f in _DIR.glob("*.md")}
    charges = set(connector_docs.DOC_SECTIONS)
    muets = sorted(sur_disque - charges)
    assert not muets, (
        f"{muets} : fichier de doc dont aucune section n'est reconnue — vérifie le "
        "format des titres (`## prerequisite — mon titre`).")


def test_chaque_fichier_correspond_a_un_connecteur_reel():
    """Le nom du fichier EST la clé de jointure. Une faute de frappe produit une doc
    que personne ne verra jamais, sans erreur nulle part."""
    orphelins = sorted({f.stem for f in _DIR.glob("*.md")} - set(providers.REGISTRY))
    assert not orphelins, (
        f"{orphelins} : doc sans connecteur correspondant au registre.")


def test_les_sections_portent_un_kind_valide_et_un_titre():
    for nom, sections in connector_docs.DOC_SECTIONS.items():
        assert sections, f"{nom} : fichier chargé mais sans section"
        for s in sections:
            assert s.kind in connector_docs.KINDS, f"{nom} : kind « {s.kind} » inconnu"
            assert s.title.strip(), f"{nom} : section sans titre"
            assert s.body_md.strip(), f"{nom}/{s.title} : section vide"


def test_le_registre_expose_bien_la_doc():
    """`Connector.doc_sections` est ce que consomment le catalogue et les fiches ;
    la propriété doit dériver du nouveau support, pas d'un vestige."""
    assert providers.REGISTRY["salesforce"].doc_sections
    assert providers.REGISTRY["serper"].doc_sections


# --- les valeurs dérivées ------------------------------------------------------

def test_lurl_de_rappel_suit_lenvironnement(monkeypatch):
    """Le bug qu'on ne veut pas réintroduire en passant au fichier : la doc
    d'atlassian et de folkmcp écrivait le domaine de PREPROD en dur, servi tel quel
    aux clients de production — qui se prenaient un `redirect_uri_mismatch` dont le
    message les accusait, eux."""
    monkeypatch.setenv("OTO_MCP_PUBLIC_URL", "https://mcp.example.test")
    corps = "\n".join(s.body_md for s in connector_docs.sections_for("atlassian"))
    assert "https://mcp.example.test/api/atlassian/oauth/callback" in corps
    assert "{{callback" not in corps, "marqueur non résolu, servi tel quel à l'utilisateur"


def test_aucun_domaine_de_callback_ecrit_en_dur():
    """TRIPWIRE. Le markdown rend l'écriture facile — donc l'écriture d'une URL en dur
    aussi. Toute URL de rappel doit passer par le marqueur."""
    coupables = []
    for f in _DIR.glob("*.md"):
        for ligne in f.read_text(encoding="utf-8").splitlines():
            if "oauth/callback" in ligne and "mcp.oto." in ligne:
                coupables.append(f"{f.name}: {ligne.strip()[:90]}")
    assert not coupables, (
        "URL de rappel écrite en dur — utilise `{{callback:/chemin}}` :\n"
        + "\n".join(coupables))


# --- robustesse du parseur -----------------------------------------------------

@pytest.mark.parametrize("tiret", ["—", "-"])
def test_les_deux_tirets_sont_acceptes(tiret):
    """On ne va pas faire échouer une doc sur un tiret cadratin absent du clavier."""
    s = connector_docs._parse(f"## usage {tiret} un titre\n\ndu corps\n", "t.md")
    assert s and s[0].kind == "usage" and s[0].title == "un titre"


def test_le_texte_hors_section_ne_disparait_pas_en_silence(caplog):
    """Il ne serait affiché nulle part : mieux vaut un avertissement qu'une perte
    invisible."""
    connector_docs._parse("du texte égaré\n\n## usage — vrai titre\n\ncorps\n", "t.md")
    assert any("hors section" in r.message for r in caplog.records)


def test_un_dossier_absent_ne_fait_pas_tomber_le_serveur(monkeypatch):
    """Fail-open : une doc manquante dégrade la fiche, elle ne casse pas le boot."""
    monkeypatch.setattr(connector_docs, "_DIR", pathlib.Path("/inexistant"))
    connector_docs._fichiers.cache_clear()
    try:
        assert connector_docs._fichiers() == {}
        assert connector_docs.sections_for("salesforce") == ()
    finally:
        connector_docs._fichiers.cache_clear()
