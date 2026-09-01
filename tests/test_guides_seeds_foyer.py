"""`oto_mcp/guides/` héberge DEUX foyers ; un seul est semé au boot.

Le lot A de #519 a déménagé les seeds de la bibliothèque publique (ex-`doctrines/`
à la racine du dépôt) sous `oto_mcp/guides/talent-sourcing/`, pour qu'il n'y ait
qu'un toit. Mais les deux foyers n'ont NI le même format de front-matter NI le
même destin :

- `oto_mcp/guides/*.md` (racine) = **guides PLATEFORME** (ADR 0042). Semés à
  CHAQUE boot par `guide_store.seed_platform_guides`, dans la table `guides`, et
  servis à tout le monde dans l'index d'`oto_guide`.
- `oto_mcp/guides/<jeu>/*.md` (sous-dossier) = **seeds de BIBLIOTHÈQUE**
  (`doctrine_library`), publiés à la main par `scripts/seed_talent_doctrines`.

Ce qui les sépare est une seule lettre de code : `list_file_guides` fait
`glob("*.md")`, NON récursif. C'est fragile de la bonne manière — mais rien ne
le disait, et un `rglob` « de propreté », ou un fichier posé à la racine par
habitude, sèmerait en production cinq guides plateforme que personne n'a
décidés. Ce test tient la frontière comme une PROPRIÉTÉ, pas comme une liste :
il ne connaît ni le nombre ni le nom des guides, donc en ajouter un ne le fait
pas rougir.
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
        f"`oto_mcp/guides/`. Semés : {sorted(servis)} ; racine : {sorted(racine)}. "
        "Si `list_file_guides` est passé en récursif, il sème désormais les jeux de "
        "bibliothèque comme des guides plateforme — servis à tous, au prochain boot.")


def test_aucun_fichier_de_sous_dossier_ne_part_au_semis_de_boot():
    servis = {g["slug"] for g in guide_store.list_file_guides()}
    sous_dossiers = {p.stem for p in DOSSIER.glob("*/*.md")}
    assert sous_dossiers, (
        "Aucun jeu de bibliothèque sous `oto_mcp/guides/` — ce test perd son objet. "
        "Si les seeds ont déménagé, déplace ce garde-fou avec eux plutôt que de le "
        "laisser passer à vide.")
    fuite = servis & sous_dossiers
    assert not fuite, (
        f"{sorted(fuite)} vient d'un SOUS-DOSSIER et serait semé comme guide "
        "plateforme au prochain boot — donc servi à tous les utilisateurs. Les "
        "sous-dossiers sont des seeds de bibliothèque publique : ils se publient à "
        "la main (`scripts/seed_talent_doctrines`), jamais au démarrage.")


def test_un_jeu_de_bibliotheque_porte_son_mode_demploi():
    """Un sous-dossier sans README, c'est le prochain fichier posé à la racine."""
    for jeu in sorted(p for p in DOSSIER.iterdir() if p.is_dir()):
        assert (jeu / "README.md").is_file(), (
            f"`{jeu.name}/` n'a pas de README.md. Il en faut un : c'est là qu'est "
            "écrit que ce dossier N'EST PAS semé au boot, et que son front-matter "
            "(slug/category/tags) n'est pas celui des guides plateforme.")


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
