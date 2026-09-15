"""Aucun lien public ne se fabrique avec un domaine deviné.

**Le défaut.** Sept endroits construisaient une URL publique sur
`os.environ.get("OTO_MCP_PUBLIC_URL", "https://mcp.oto.ninja")` — le même littéral,
recopié de voisin en voisin. Sans la variable, chacun rendait donc une adresse **chez
nous, en préproduction**, avec l'assurance d'une adresse juste : une redirection OAuth
enregistrée au byte près chez un fournisseur, un rappel de paiement, un jeton de
téléversement, le suffixe d'hôte qui épingle une org. Une instance servie ailleurs que
chez nous aurait envoyé son fournisseur d'identité et son prestataire de paiement
frapper à NOTRE porte.

Ce qui rend ce défaut particulier, c'est qu'il se propage par imitation : le septième
exemplaire a été écrit le 15/09/2026 par quelqu'un qui copiait le patron voisin. La
réponse n'est donc pas de corriger cinq lignes mais de supprimer le littéral à recopier —
une source unique, `config.public_base_url()`, qui **lève** au lieu de supposer.

Ce banc décrit ce qui doit devenir vrai. Il vise l'acte, pas la forme du code : chaque
fabricant de lien, privé de la variable, doit refuser — et jamais rendre un lien qui
porte l'un de nos domaines.
"""
from __future__ import annotations

import os

import pytest


from oto_mcp import config


@pytest.fixture(autouse=True)
def _secret_d_instance(monkeypatch):
    """La clé qui signe, déclarée pour CE fichier.

    ⚠️ Elle était posée par `os.environ.setdefault` au niveau module — donc dès la
    collecte, et pour tout ce que pytest importait ensuite. Un banc qui signait sans
    déclarer de clé héritait de celle-ci et passait en suite complète tout en rougissant
    lancé seul (même motif que l'adresse publique, colmaté le 15/09/2026)."""
    monkeypatch.setenv("OTO_MCP_OAUTH_STATE_SECRET", "test-secret")


NOS_DOMAINES = ("oto.ninja", "oto.cx", "oto.zone", "otomata.tech")
_BASE = "https://mcp.partenaire.example"


@pytest.fixture(autouse=True)
def _sans_declaration(monkeypatch):
    monkeypatch.delenv("OTO_MCP_PUBLIC_URL", raising=False)
    return monkeypatch


def _fabricants():
    """Les fonctions qui construisent une adresse publique, et ce qu'on en attend.

    Importées ici plutôt qu'en tête : plusieurs tirent des modules lourds, et un banc
    qui décrit un refus n'a pas à payer le catalogue entier pour le dire."""
    from oto_mcp import billing, outreach_optout, subdomain_org
    from oto_mcp.auth import flow, google
    return [
        ("redirection OAuth générique", lambda: flow.redirect_uri("/api/x/callback"),
         f"{_BASE}/api/x/callback"),
        ("désinscription des relances", lambda: outreach_optout.lien("u1").rsplit("/", 1)[0],
         f"{_BASE}/o/u"),
        ("désinscription du digest", lambda: outreach_optout.lien_digest("u1").rsplit("/", 1)[0],
         f"{_BASE}/o/d"),
        ("redirection OAuth Google", google._redirect_uri,
         f"{_BASE}/api/google/oauth/callback"),
        ("rappel de paiement", billing.webhook_url,
         f"{_BASE}/api/billing/webhook"),
        ("suffixe d'hôte qui épingle une org", subdomain_org._suffix,
         "--mcp.partenaire.example"),
    ]


@pytest.mark.parametrize("nom, fabrique, attendu", _fabricants(),
                         ids=[n for n, _, _ in _fabricants()])
def test_sans_la_variable_il_refuse_au_lieu_d_inventer(nom, fabrique, attendu):
    with pytest.raises(RuntimeError) as e:
        fabrique()
    assert "OTO_MCP_PUBLIC_URL" in str(e.value), "le refus doit nommer ce qui manque"


@pytest.mark.parametrize("nom, fabrique, attendu", _fabricants(),
                         ids=[n for n, _, _ in _fabricants()])
def test_avec_la_variable_il_suit_l_instance_et_pas_nos_domaines(
        nom, fabrique, attendu, monkeypatch):
    """Une instance servie ailleurs fabrique ses liens sur SON domaine. C'est la moitié
    utile du correctif : refuser sans la variable ne sert à rien si, avec elle, un
    littéral continuait de gagner quelque part."""
    monkeypatch.setenv("OTO_MCP_PUBLIC_URL", _BASE + "/")
    rendu = fabrique()
    assert rendu == attendu
    assert not any(d in rendu for d in NOS_DOMAINES), f"{nom} porte encore un de nos domaines"


def test_le_jeton_de_televersement_refuse_aussi(monkeypatch):
    """Celui-là ne s'appelle pas nu — il passe par la capacité, avec son autorisation
    déjà accordée. Sans base publique, le jeton est signé pour une adresse inventée : on
    refuse AVANT de rendre l'URL, pas après l'avoir servie."""
    from oto_mcp import upload_tokens as ut
    from oto_mcp.capabilities import uploads as U
    from oto_mcp.capabilities._types import ResolvedCtx

    monkeypatch.setattr(ut, "check_target_access", lambda sub, target: None)
    with pytest.raises(RuntimeError) as e:
        U._upload_url(ResolvedCtx(sub="u1", org_id=42),
                      U.UploadUrlInput(target="doc", op="create", project_id=5,
                                       title="Transcript"))
    assert "OTO_MCP_PUBLIC_URL" in str(e.value)


def test_la_source_unique_existe_et_normalise(monkeypatch):
    """Les cinq sites tombaient aussi d'accord sur un détail : retirer la barre finale.
    Elle appartient à la source, sinon chaque appelant la refait — et l'un l'oubliera."""
    monkeypatch.setenv("OTO_MCP_PUBLIC_URL", _BASE + "///")
    assert config.public_base_url() == _BASE
    monkeypatch.setenv("OTO_MCP_PUBLIC_URL", _BASE)
    assert config.public_base_url() == _BASE
    assert config.public_host() == "mcp.partenaire.example"


# ── La garde qui empêche le huitième exemplaire ───────────────────────────────

def test_aucune_adresse_publique_ne_se_fabrique_sur_un_defaut():
    """Corriger les sept sites ne suffit pas : le défaut se propageait par imitation, et
    le prochain qui écrira une fonction voisine copiera son voisin. Cette garde refuse la
    huitième copie.

    Elle vise l'ACTE, pas le littéral : ce qui est interdit, c'est de donner à
    `OTO_MCP_PUBLIC_URL` un défaut **qui ressemble à une adresse**. Un défaut vide reste
    permis, et c'est voulu — deux endroits s'en servent pour REFUSER d'inventer plutôt
    que pour inventer : le déclencheur de webhook rend alors une URL relative, et le
    contrôle Apollo compose un ensemble d'hôtes à refuser. Aucun des deux ne promet une
    adresse. Formulée sur un littéral, cette garde aurait dû les exclure par leur nom de
    fichier — or un chemin se renomme et l'exception survit à sa raison."""
    import ast
    import pathlib

    racine = pathlib.Path(__file__).resolve().parent.parent / "oto_mcp"
    coupables = []
    for fichier in sorted(racine.rglob("*.py")):
        arbre = ast.parse(fichier.read_text(encoding="utf-8"), filename=str(fichier))
        for n in ast.walk(arbre):
            if not (isinstance(n, ast.Call) and len(n.args) == 2
                    and isinstance(n.func, ast.Attribute) and n.func.attr == "get"
                    and isinstance(n.args[0], ast.Constant)
                    and n.args[0].value == "OTO_MCP_PUBLIC_URL"):
                continue
            defaut = n.args[1]
            if isinstance(defaut, ast.Constant) and isinstance(defaut.value, str) and defaut.value:
                coupables.append(
                    f"{fichier.relative_to(racine.parent)}:{n.lineno} → défaut {defaut.value!r}")

    assert not coupables, (
        "une adresse publique est fabriquée sur un défaut :\n    "
        + "\n    ".join(coupables)
        + "\n  Sans la variable, ce code rend une adresse CHEZ NOUS avec l'assurance"
          "\n  d'une adresse juste — sur une instance servie ailleurs, il envoie un tiers"
          "\n  frapper à notre porte sans qu'aucune erreur ne le dise."
          "\n  → `config.public_base_url()` (ou `public_host()`), qui lève en nommant"
          "\n    la variable. Si l'absence doit être TOLÉRÉE, que le défaut soit vide et"
          "\n    que le code rende une adresse relative, jamais un domaine inventé.")
