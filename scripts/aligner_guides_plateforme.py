"""Le geste UNIQUE qui aligne les guides plateforme déjà en base (otomata-tech/oto#236).

Le semis de démarrage pose désormais l'empreinte du fichier qu'il écrit
(`props->>'seed_sha256'`) et s'en sert pour décider : fichier changé et base intacte →
mise à jour ; base éditée → conservée et signalée ; même empreinte → rien. Mais TOUTE
la population d'avant ce lot est sans empreinte, et le démarrage ne devine pas — il ne
saurait pas distinguer « jamais touché depuis le semis » de « réécrit par un admin ».

Ce script tranche, à la main, une fois, après livraison :

1. il ÉNUMÈRE la population (fichiers du dépôt ∪ guides plateforme en base) ;
2. il MONTRE chaque écart (présent d'un seul côté, prose différente, empreinte absente) ;
3. avec `--aligner`, il écrit — et seulement alors.

Deux sens, et ils ne sont pas symétriques :

- **fichier → base** : la base prend la prose du dépôt et son empreinte. C'est le cas
  ordinaire : le dépôt est relu en PR, la base avait dérivé faute de mise à jour.
- **base → fichier** : un guide plateforme SERVI sans fichier dans le dépôt (cas mesuré
  le 13/09/2026 : `procedure-en-routine`) est exporté en `oto_mcp/guides/<slug>.md`,
  qui devient sa source de semis. Aucun guide plateforme ne reste servi sans source
  versionnée — sinon il n'est relu par personne et le prochain environnement ne l'a pas.
  ⚠️ Le fichier écrit est à COMMITTER : il ne vaut que versionné.

Usage (sur la box) :
    cd /opt/oto-mcp && ./.venv/bin/python -m scripts.aligner_guides_plateforme          # montre
    cd /opt/oto-mcp && ./.venv/bin/python -m scripts.aligner_guides_plateforme --aligner # écrit
"""
from __future__ import annotations

import argparse
import difflib
import sys
from pathlib import Path

from oto_mcp import db, guide_store

_DOSSIER = Path(guide_store.__file__).resolve().parent / "guides"


def _fichier_de(guide: dict) -> str:
    """Le markdown à écrire pour un guide qui n'existe qu'en base — front-matter au
    format que `guide_store._parse` relit, pour que l'export soit immédiatement une
    source de semis valide."""
    entete = [f"title: {guide.get('title') or guide['slug']}"]
    if (guide.get("description") or "").strip():
        entete.append(f"description: {guide['description'].strip()}")
    return "---\n" + "\n".join(entete) + "\n---\n\n" + (guide.get("body_md") or "").strip() + "\n"


def _ecart(avant: str, apres: str) -> str:
    lignes = list(difflib.unified_diff(avant.splitlines(), apres.splitlines(),
                                       "base", "fichier", lineterm="", n=0))
    tete = lignes[:20]
    return "\n".join(f"      {l}" for l in tete) + (
        f"\n      … ({len(lignes) - 20} lignes de diff de plus)" if len(lignes) > 20 else "")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--aligner", action="store_true",
                    help="écrit (sans lui : n'affiche que ce qui serait fait)")
    args = ap.parse_args(argv)

    fichiers = {g["slug"]: g for g in guide_store.list_file_guides()}
    en_base = {g["slug"]: g for g in db.list_guides_db("platform", guide_store.PLATFORM_OWNER)}
    population = sorted(set(fichiers) | set(en_base))
    print(f"{len(population)} guides plateforme : "
          f"{len(fichiers)} fichier(s), {len(en_base)} en base.\n")

    a_aligner: list[str] = []
    a_exporter: list[str] = []
    for slug in population:
        f, b = fichiers.get(slug), en_base.get(slug)
        if b is None:
            print(f"  {slug} : fichier SEUL — le prochain démarrage le sèmera.")
            continue
        if f is None:
            print(f"  {slug} : EN BASE SEULEMENT — servi sans source versionnée, "
                  f"à exporter en {_DOSSIER.name}/{slug}.md.")
            a_exporter.append(slug)
            continue
        empreinte_base = db.empreinte_de_couche(b.get("title") or "",
                                                b.get("description") or "",
                                                b.get("body_md") or "")
        if empreinte_base == f["seed_sha256"]:
            posee = (b.get("seed_sha256") or "").strip()
            if posee == f["seed_sha256"]:
                print(f"  {slug} : identique, empreinte posée — rien à faire.")
            else:
                print(f"  {slug} : identique, empreinte ABSENTE — à estampiller.")
                a_aligner.append(slug)
            continue
        print(f"  {slug} : ÉCART base ↔ fichier "
              f"({len((b.get('body_md') or '').splitlines())} lignes en base, "
              f"{len(f['body_md'].splitlines())} dans le fichier)")
        print(_ecart(b.get("body_md") or "", f["body_md"]))
        a_aligner.append(slug)

    print(f"\n{len(a_aligner)} à aligner (fichier → base), "
          f"{len(a_exporter)} à exporter (base → fichier).")
    if not args.aligner:
        print("Rien écrit. Rejoue avec --aligner pour écrire.")
        return 0

    for slug in a_aligner:
        f = fichiers[slug]
        if not db.aligner_guide_db("platform", guide_store.PLATFORM_OWNER, slug,
                                   f["body_md"], f["title"], f["description"],
                                   seed_sha256=f["seed_sha256"]):
            print(f"  ! {slug} : aucune couche on-demand à ce slug — non aligné.")
            continue
        print(f"  ✓ {slug} aligné sur le fichier.")
    for slug in a_exporter:
        g = en_base[slug]
        cible = _DOSSIER / f"{slug}.md"
        cible.write_text(_fichier_de(g), encoding="utf-8")
        # Et on ESTAMPILLE : sans empreinte, le démarrage suivant reverrait ce guide
        # comme « posé avant le semis » alors que son fichier vient d'en naître. La
        # prose écrite est celle de la base — l'export ne change pas ce qui est servi.
        db.aligner_guide_db("platform", guide_store.PLATFORM_OWNER, slug,
                            g.get("body_md") or "", g.get("title") or "",
                            g.get("description") or "",
                            seed_sha256=db.empreinte_de_couche(
                                g.get("title") or "", g.get("description") or "",
                                g.get("body_md") or ""))
        print(f"  ✓ {slug} exporté dans {cible} et estampillé — À COMMITTER.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
