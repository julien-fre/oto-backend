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
import re

import pytest

from oto_mcp import providers
from oto_mcp.connectors import docs_reader as connector_docs

_DIR = pathlib.Path(connector_docs.__file__).parent / "docs"
_PROVIDERS_DIR = pathlib.Path(providers.__file__).parent


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


def test_chaque_section_servie_vient_de_son_seul_markdown():
    """UN domicile, exactement : ce que le catalogue sert pour un connecteur est
    EXACTEMENT ce que `connector_docs/<nom>.md` contient — pas une section de plus
    (une seconde source qui s'ajouterait), pas une de moins.

    `Connector.doc_sections` est la propriété que consomment le catalogue public et les
    fiches ; ce test la tient à sa source unique. Un jour où l'on y ajouterait un repli
    (« si pas de markdown, prendre la constante du module de déclaration »), la doc
    aurait deux domiciles et plus personne ne saurait lequel est servi."""
    for nom, c in providers.REGISTRY.items():
        servies = c.doc_sections
        if not servies:
            continue
        fichier = _DIR / f"{nom}.md"
        assert fichier.is_file(), (
            f"{nom} : le catalogue sert {len(servies)} section(s) sans "
            f"connectors/docs/{nom}.md — il existe donc une SECONDE source")
        attendues = tuple(
            connector_docs.DocSection(s.kind, s.title, connector_docs._resoudre(s.body_md))
            for s in connector_docs._parse(fichier.read_text(encoding="utf-8"), fichier.name))
        assert servies == attendues, (
            f"{nom} : les sections servies diffèrent de connectors/docs/{nom}.md")


def test_la_prose_ne_se_pose_pas_dans_le_module_de_declaration():
    """TRIPWIRE — depuis que le registre est un fichier par connecteur, poser la doc
    à côté de `CONNECTOR` dans `providers/<nom>.py` est le geste naturel. Un audit du
    27/08/2026 l'a proposé tel quel, sur la foi d'une docstring périmée qui logeait
    encore la prose dans `connectors/docs_reader.py`.

    Rien ne la lirait : `_fichiers()` globe `connectors/docs/*.md` et rien d'autre. La
    fiche s'afficherait sans doc, sans erreur nulle part — le pire mode d'échec, celui
    que personne ne remarque."""
    coupables = []
    for f in sorted(_PROVIDERS_DIR.glob("*.py")):
        txt = f.read_text(encoding="utf-8")
        for constante in ("DOC_SECTIONS", "DOC_SECTION", "DOCS", "DOC"):
            if re.search(rf"^{constante}\s*[:=]", txt, re.M):
                coupables.append(
                    f"providers/{f.name} : constante {constante} — la prose d'un "
                    f"connecteur va dans connectors/docs/{f.stem}.md, pas dans son "
                    "module de déclaration (rien ne l'y lirait)")
    assert not coupables, "\n".join(coupables)


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
