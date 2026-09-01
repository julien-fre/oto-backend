"""Qualifier les comptes d'un tenant AVANT de le déclarer (ADR 0052 L3bis).

Déclarer un tenant qualifie ses identifiants (`x` → `slug:x`). Tout ce qui existait
sous la forme nue doit donc être repointé — sinon, au premier login qui suit, chaque
utilisateur tombe sur un compte vide et son organisation reste derrière.

**L'ordre est l'invariant de ce lot : migrer D'ABORD, basculer ENSUITE.** Pendant
l'avant-bascule, les jetons vivants portent encore la forme nue et traversent
`resolve_sub` par l'alias posé ici — le trafic ne voit rien passer. L'inverse
(basculer puis migrer) ouvre une fenêtre où un identifiant qualifié frappe des
données non migrées, et fabrique des comptes vides que personne ne pourra recoller.

Le mapping est un FICHIER, pas une dérivation : décider que deux identifiants sont
la même personne est un acte humain (ADR 0052 R3 — le merge automatique par email
franchirait les tenants, ce que le §6 interdit). Le script vérifie, il ne devine pas.

    # 1. voir ce qui serait fait (défaut : rien n'est écrit)
    ./.venv/bin/python deploy/migrate_tenant_subs.py mapping.json
    # 2. appliquer
    ./.venv/bin/python deploy/migrate_tenant_subs.py mapping.json --apply

Format du mapping :

    {"slug": "acme",
     "comptes": [{"old": "vnv1hpyutcpn", "new": "acme:vnv1hpyutcpn",
                  "email": "…", "note": "identifiants identiques des deux côtés"}]}

`email` n'est PAS utilisé pour décider — il sert à ce qu'un humain relise le fichier
et reconnaisse les personnes. Le script refuse si l'email ne correspond pas au compte
visé : c'est un détecteur de faute de frappe, pas un critère de fusion.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from oto_mcp.db import users as db_users          # noqa: E402
from oto_mcp.db._conn import _connect             # noqa: E402

SOURCE = "migration:l3bis"


def _charger(chemin: str) -> tuple[str, list]:
    donnees = json.loads(Path(chemin).read_text(encoding="utf-8"))
    slug = str(donnees.get("slug") or "").strip()
    comptes = donnees.get("comptes") or []
    if not slug:
        raise SystemExit("mapping : `slug` manquant")
    if not comptes:
        raise SystemExit("mapping : aucun compte")
    for i, c in enumerate(comptes):
        for champ in ("old", "new", "email"):
            if not str(c.get(champ) or "").strip():
                raise SystemExit(f"mapping : compte #{i} sans `{champ}`")
        if not str(c["new"]).startswith(f"{slug}:"):
            raise SystemExit(
                f"mapping : `{c['new']}` ne porte pas le préfixe `{slug}:` — "
                "un identifiant qualifié le porte TOUJOURS, sinon la bascule ne le "
                "retrouvera pas.")
        if c["old"] == c["new"]:
            raise SystemExit(f"mapping : `{c['old']}` migre vers lui-même")
    olds = [c["old"] for c in comptes]
    if len(set(olds)) != len(olds):
        raise SystemExit("mapping : un même identifiant d'origine apparaît deux fois")
    return slug, comptes


def _etat(conn, sub: str) -> dict | None:
    row = conn.execute(
        "SELECT sub, email, name, role FROM users WHERE sub=%s", (sub,)).fetchone()
    return dict(row) if row else None


def _controler(comptes: list) -> list:
    """Ce que la base dit de chaque ligne du mapping. N'écrit rien."""
    rapport = []
    with _connect() as conn:
        for c in comptes:
            old, new = _etat(conn, c["old"]), _etat(conn, c["new"])
            if old is None:
                verdict = "déjà migré (ou inconnu)" if new else "INTROUVABLE"
            elif (old.get("email") or "").lower() != c["email"].lower():
                verdict = f"EMAIL DIVERGENT (base : {old.get('email')})"
            elif new is None:
                verdict = "à migrer (la cible sera créée)"
            else:
                verdict = "à migrer (la cible existe déjà)"
            rapport.append({**c, "verdict": verdict, "cible_existe": new is not None})
    return rapport


def _creer_cible(conn, source: dict, new_sub: str) -> None:
    """Crée la ligne d'arrivée SANS passer par `upsert_user`.

    `upsert_user` déclenche la réconciliation d'invitation et la création d'une org
    maison : deux effets de bord indésirables ici, puisque le compte reprend ceux de
    l'ancien une seconde plus tard. On copie donc l'identité, et rien d'autre.
    """
    conn.execute(
        "INSERT INTO users (sub, email, name, role) VALUES (%s,%s,%s,%s) "
        "ON CONFLICT (sub) DO NOTHING",
        (new_sub, source.get("email"), source.get("name"), source.get("role") or "member"))


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    appliquer = "--apply" in sys.argv
    if not args:
        print(__doc__)
        return 2

    slug, comptes = _charger(args[0])
    rapport = _controler(comptes)

    print(f"### Tenant « {slug} » — {len(rapport)} comptes\n")
    for r in rapport:
        print(f"   {r['email']:<34} {r['old']:<16} → {r['new']:<24} {r['verdict']}")
        if r.get("note"):
            print(f"      ↳ {r['note']}")

    bloquants = [r for r in rapport if r["verdict"].isupper()
                 or r["verdict"].startswith("EMAIL")]
    if bloquants:
        print(f"\n⚠️  {len(bloquants)} ligne(s) bloquante(s) — rien n'a été écrit.")
        return 1

    a_faire = [r for r in rapport if r["verdict"].startswith("à migrer")]
    if not appliquer:
        print(f"\n(simulation) {len(a_faire)} compte(s) seraient migrés. "
              "Relancer avec --apply pour écrire.")
        return 0

    print(f"\n### Migration de {len(a_faire)} compte(s)\n")
    faits, echecs = 0, []
    for r in a_faire:
        try:
            if not r["cible_existe"]:
                with _connect() as conn:
                    src = _etat(conn, r["old"])
                    if src is None:                     # course : déjà migré entre-temps
                        continue
                    _creer_cible(conn, src, r["new"])
            ok = db_users.migrate_sub(r["old"], r["new"], operator_source=SOURCE)
            print(f"   {'✓' if ok else '·'} {r['email']:<34} {r['old']} → {r['new']}")
            faits += int(bool(ok))
        except Exception as e:                          # une ligne ne bloque pas le lot
            echecs.append((r["email"], str(e).splitlines()[0][:140]))
            print(f"   ✗ {r['email']:<34} {str(e).splitlines()[0][:100]}")

    print(f"\n{faits} migré(s), {len(echecs)} en échec.")
    if echecs:
        print("\n⚠️  À traiter avant le flip — un compte non migré perdra son "
              "organisation au premier login qualifié :")
        for email, err in echecs:
            print(f"   {email} : {err}")
        return 1
    print("\nLes alias sont posés : le trafic vivant (jetons de forme nue) est drainé.")
    print("Le flip peut suivre — retrait du drain + ligne `tenants` + restart.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
