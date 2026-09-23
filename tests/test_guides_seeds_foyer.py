"""Le semis de démarrage ne prend QUE les `*.md` de la racine de `oto_mcp/guides/`.

Ce qui tient cette frontière est une seule lettre de code : `list_file_guides` fait
`glob("*.md")`, NON récursif. C'est fragile de la bonne manière — mais rien ne le
disait, et un `rglob` « de propreté », ou un dossier de seeds posé là par habitude,
sèmerait en production des guides PLATEFORME que personne n'a décidés : servis à
tous, dans l'index d'`oto_guide`, au prochain démarrage.

⚠️ Le dossier `talent-sourcing/` — cinq guides de bibliothèque publique et leur
README, publiés à la main par `scripts/seed_talent_doctrines` — a été RETIRÉ du dépôt
le 23/09/2026 (décision produit du 13/09, otomata-tech/oto#240) : aucun n'avait jamais
été publié, et le script n'était pas sûr à rejouer. Il n'y a donc plus de second foyer
sous `oto_mcp/guides/`. La frontière, elle, reste : elle se tient ici comme une
PROPRIÉTÉ du code, sur un dossier de banc, et non plus comme un constat sur un
sous-dossier qui existait — un garde-fou qui s'éteint avec son exemple ne garde rien.
"""
from __future__ import annotations

import pathlib

from oto_mcp import guide_store

DOSSIER = pathlib.Path(guide_store.__file__).resolve().parent / "guides"


def test_les_seeds_semes_au_boot_sont_exactement_les_md_de_la_racine():
    servis = {g["slug"] for g in guide_store.list_file_guides()}
    racine = {p.stem for p in DOSSIER.glob("*.md")}
    assert servis == racine, (
        "Le semis de boot ne correspond plus aux fichiers de la RACINE de "
        f"`oto_mcp/guides/`. Semés : {sorted(servis)} ; racine : {sorted(racine)}.")


def test_le_semis_NE_DESCEND_PAS_dans_un_sous_dossier(tmp_path, monkeypatch):
    """La propriété, sur un dossier à nous : la racine part, le sous-dossier reste.

    Testée sur un dossier de banc et non sur `oto_mcp/guides/` : le jour où le dépôt
    n'a plus de sous-dossier — c'est le cas depuis le 23/09/2026 — un test qui regarde
    le vrai dossier passe à vide et ne garde plus rien."""
    monkeypatch.setattr(guide_store, "_GUIDES_DIR", tmp_path)
    (tmp_path / "a-la-racine.md").write_text("---\ntitle: T\n---\ncorps",
                                             encoding="utf-8")
    (tmp_path / "jeu").mkdir()
    (tmp_path / "jeu" / "dans-un-jeu.md").write_text("---\ntitle: T\n---\ncorps",
                                                     encoding="utf-8")
    assert [g["slug"] for g in guide_store.list_file_guides()] == ["a-la-racine"], (
        "`list_file_guides` est passé en RÉCURSIF : il sème désormais les jeux de "
        "bibliothèque comme des guides plateforme — servis à tous, au prochain boot.")


# --------------------------------------------------------------------------- #
# Un pointeur de docstring vers un guide doit désigner un guide QUI EXISTE
# --------------------------------------------------------------------------- #
#
# Les docstrings des gros connecteurs renvoient au guide qui porte leur mode
# d'emploi (`oto_guide op=read slug="…"`), pour ne pas payer la prose dans le
# handshake de chaque tour. Le renvoi est du TEXTE : rien ne le relie au fichier,
# et un guide renommé ou jamais écrit laisse une docstring qui envoie l'agent
# vers une porte fermée — pire que pas de renvoi du tout, parce qu'il aura
# dépensé un appel pour l'apprendre.
#
# Ce garde-fou est auto-maintenu : ajouter un guide et le citer le garde vert
# sans y toucher ; il ne tombe que sur un renvoi orphelin.

def test_tout_renvoi_de_docstring_vers_un_guide_designe_un_guide_existant():
    import pathlib
    import re

    from oto_mcp import guide_store

    slugs = {g["slug"] for g in guide_store.list_file_guides()}
    motif = re.compile(r'oto_guide\s+op=read\s+slug="([^"]+)"')

    orphelins = []
    for f in sorted((pathlib.Path(__file__).parent.parent / "oto_mcp" / "tools")
                    .glob("*.py")):
        for slug in motif.findall(f.read_text(encoding="utf-8")):
            if slug not in slugs:
                orphelins.append(f"{f.name} → slug '{slug}'")

    assert not orphelins, (
        f"renvoi(s) vers un guide inexistant : {orphelins}. Les slugs de fichiers "
        f"disponibles sont {sorted(slugs)}.")
