"""Seed des guides PARTAGÉS « talent sourcing / RH / ATS » dans la
bibliothèque publique, en tant qu'auteur **Otomata**.

Contrairement à `seed_doctrine_library` (qui publie les skills d'une org
existante), ce script publie directement des guides **versionnés au repo**
(`oto_mcp/guides/talent-sourcing/*.md`) — pas besoin d'org source. Idempotent :
`publish_guide` fait un upsert par slug (ré-exécuter incrémente la version
sans dupliquer).

⚠️ Ces fichiers vivent dans un SOUS-dossier de `oto_mcp/guides/`, et c'est un
choix : `guide_store.list_file_guides` ne balaie que `guides/*.md` (glob NON
récursif), donc ce qui est ici n'est PAS semé au boot comme guide plateforme.
Un fichier posé à la RACINE de `oto_mcp/guides/` le serait, lui — le garde-fou
est `tests/test_guides_seeds_foyer.py`.

Chaque markdown porte un front-matter `---` (slug, title, description, category,
tags), parsé sans dépendance externe (pas de pyyaml requis).

Usage (sur la box) :
    cd /opt/oto-mcp && ./.venv/bin/python -m scripts.seed_talent_doctrines [dir]

`dir` par défaut = `oto_mcp/guides/talent-sourcing/` relatif à la racine du repo.
"""
from __future__ import annotations

import sys
from pathlib import Path

from oto_mcp import db, org_store

# Racine repo = parent de scripts/. Le dossier des guides partagés.
_DEFAULT_DIR = (Path(__file__).resolve().parent.parent
                / "oto_mcp" / "guides" / "talent-sourcing")


def parse_front_matter(text: str) -> tuple[dict, str]:
    """Sépare un front-matter `---\\nkey: value\\n---` du corps markdown.

    Parser minimal (pas de pyyaml) : `key: value` par ligne, `tags` éclaté sur la
    virgule. Si pas de front-matter, renvoie `({}, text)`."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, text
    meta: dict = {}
    body_start = len(lines)
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            body_start = i + 1
            break
        if ":" in lines[i]:
            key, _, val = lines[i].partition(":")
            key, val = key.strip(), val.strip()
            if key == "tags":
                meta[key] = [t.strip() for t in val.split(",") if t.strip()]
            else:
                meta[key] = val
    body = "\n".join(lines[body_start:]).strip()
    return meta, body


def main() -> None:
    src_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else _DEFAULT_DIR
    if not src_dir.is_dir():
        print(f"dossier introuvable : {src_dir}", file=sys.stderr)
        sys.exit(2)

    db.init_db()

    files = sorted(src_dir.glob("*.md"))
    if not files:
        print(f"aucun .md dans {src_dir}", file=sys.stderr)
        return

    published = 0
    for path in files:
        meta, body = parse_front_matter(path.read_text(encoding="utf-8"))
        slug = meta.get("slug") or path.stem
        if not body.strip():
            print(f"  ⚠ {path.name} : corps vide, ignoré", file=sys.stderr)
            continue
        row = org_store.publish_guide(
            slug=slug,
            title=meta.get("title") or "",
            description=meta.get("description") or "",
            body_md=body,
            author_kind="otomata",
            author_org_id=None,
            author_display="Otomata",
            category=meta.get("category") or "Recrutement",
            tags=meta.get("tags") or [],
            visibility="public",
            published_by="seed:talent",
        )
        published += 1
        print(f"  ✓ {slug} → bibliothèque (entrée #{row['id']} v{row['version']})")

    print(f"\n{published} doctrine(s) partagée(s) publiée(s) sous l'auteur Otomata.")


if __name__ == "__main__":
    main()
