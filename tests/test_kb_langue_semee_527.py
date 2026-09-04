"""La base de connaissance d'une org naît en FRANÇAIS, quelle que soit sa langue.

`oto_kb op="ensure" create_shared=true` sème toute nouvelle base avec un nom et un
résumé français codés en dur : « Base de connaissance » et « La base de connaissance de
l'organisation : pages de référence partagées (processus, contexte, conventions).
Une seule par org. » Reproduit sur trois orgs clientes anglophones, dont deux ont
déjà des membres extérieurs — et c'est la PREMIÈRE ligne de leur écran de projets,
au-dessus d'une base entièrement anglaise. Une quatrième org ne lit « Knowledge
base » que parce que quelqu'un l'a renommée à la main (#527).

**Pourquoi l'anglais, et pas « la langue de l'org ».** La plateforme n'a aucun
signal de langue honnête : mesuré le 2026-09-02, `users.locale` n'est posée que
sur une poignée de comptes, `billing_identities` est à zéro ligne, et le TLD de
l'adresse ne tranche rien. Pire ici : la KB appartient à l'ORG, alors que
`op="ensure"` est appelé par UN membre — déduire la langue d'une org de la
préférence d'interface d'un de ses membres serait une devinette déguisée en
donnée. L'anglais est en revanche déjà la langue de la surface servie : les
descriptions d'outils, l'OpenAPI et les libellés que cette même org lit partout
ailleurs le sont.

**Pourquoi pas « les deux vides », la 3ᵉ option du signalement.** Le front ne
libelle pas la KB : `DocumentsView.vue` résout l'ancre et REDIRIGE vers la page
projet générique, qui affiche `projects.name`. Un nom vide ferait une ligne sans
titre dans l'écran de projets et une page sans titre — pire que le mauvais idiome.

Ce banc est un CLIQUET, pas un classifieur de langue : cf. `_marqueurs_fr`.
"""
from __future__ import annotations

import re

import pytest

from oto_mcp.capabilities import kb as K
from oto_mcp.capabilities._types import ResolvedCtx

# Heuristique ASSUMÉE. On ne détecte pas « du français » — on détecte ce que ces
# deux chaînes-là avaient de français : tout caractère non-ASCII (accents et
# guillemets typographiques « »), l'élision en début de mot (« l'organisation »,
# « d'org ») et le mot « connaissance ». Un texte français sans accent ni élision
# passerait ; le but est d'empêcher la RÉINTRODUCTION du libellé retiré, pas de
# juger une langue.
# ⚠️ L'élision se teste en DÉBUT de mot (`\b`) : sans ça, le possessif anglais
# « organization's » porte un « n' » et le cliquet crierait sur de l'anglais.
_ELISION = re.compile(r"\b(?:l|d|n|qu|j|m|t|s)'", re.IGNORECASE)


def _marqueurs_fr(s: str) -> list[str]:
    trouves = [c for c in s if ord(c) > 127]
    trouves += _ELISION.findall(s)
    if "connaissance" in s.lower():
        trouves.append("connaissance")
    return trouves


@pytest.fixture
def seams(monkeypatch):
    """Seams db/org_store stubés — même patron que `tests/test_kb.py`, aucune base."""
    rec = {"created": [], "anchor": None, "projects": {}}

    monkeypatch.setattr(K.org_store, "get_kb_project_id", lambda org: rec["anchor"])

    def _claim(org, pid):
        rec["anchor"] = pid
        return True

    monkeypatch.setattr(K.org_store, "claim_kb_project", _claim)

    def _create(ot, oid, name, brief, created_by=None):
        pid = 42
        rec["created"].append({"name": name, "brief": brief})
        rec["projects"][pid] = {"id": pid, "name": name, "brief_md": brief,
                                "owner_type": ot, "owner_id": oid, "archived_at": None}
        return pid

    monkeypatch.setattr(K.db, "create_project", _create)
    monkeypatch.setattr(K.db, "get_project_by_id", lambda pid: rec["projects"].get(pid))
    monkeypatch.setattr(K.db, "log_project_activity", lambda *a, **k: None)
    return rec


def test_la_kb_semee_ne_parle_pas_francais(seams):
    """Le nom ET le résumé écrits en base pour une org neuve, tels quels.

    C'est ce couple exact que le client anglophone voit en tête de son écran de
    projets — pas une valeur par défaut d'affichage, mais la ligne `projects`
    réellement créée par `op="ensure"`.

    ⚠️ `create_shared=True` depuis le 04/09 : créer la base d'org n'est plus le
    défaut d'`ensure` (elle est visible de TOUS les membres, et le verbe sonnait
    comme une vérification). Ce banc n'a pas d'avis là-dessus — il a besoin d'une
    création pour lire ce qu'elle sème, et le demande donc explicitement."""
    out = K._kb(ResolvedCtx(sub="u1", org_id=7),
                K.KbInput(op="ensure", create_shared=True))
    assert len(seams["created"]) == 1
    seme = seams["created"][0]
    assert _marqueurs_fr(seme["name"]) == [], f"nom semé en français : {seme['name']!r}"
    assert _marqueurs_fr(seme["brief"]) == [], f"résumé semé en français : {seme['brief']!r}"
    # Et c'est bien ce nom-là qui est rendu à l'appelant (donc au front).
    assert out["name"] == seme["name"] and out["brief_md"] == seme["brief"]


def test_le_libelle_servi_avant_toute_creation_est_le_meme(seams):
    """`op="get"` sur une org SANS KB rend un libellé de proposition, sans rien créer.

    Ce libellé est le seul texte que le front reçoit tant que la base n'existe
    pas ; il doit être celui qui sera réellement semé, dans la même langue."""
    out = K._kb(ResolvedCtx(sub="u1", org_id=7), K.KbInput(op="get"))
    assert out["project_id"] is None and seams["created"] == []
    assert _marqueurs_fr(out["name"]) == [], f"libellé servi en français : {out['name']!r}"


def test_la_description_de_loutil_ne_nomme_plus_la_kb_en_francais():
    """Une description d'outil est une INSTRUCTION relue à chaque appel.

    Celle de `me.kb` annonçait au modèle « un projet dédié « Base de connaissance » ».
    Aucune KB neuve ne porte plus ce nom, et le nom n'a jamais été la clé de
    résolution (l'ancre `orgs.kb_project_id` l'est depuis le lot 3) : laisser cette
    promesse dans le texte servi apprend au modèle à chercher par un nom faux."""
    cap = next(c for c in K.CAPABILITIES if c.key == "me.kb")
    assert "Base de connaissance" not in cap.description
