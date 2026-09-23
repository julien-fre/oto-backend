#!/usr/bin/env python3
"""Réécrit `namespace` → `datastore` dans les CONTENUS servis aux agents.

⚠️ **À BLANC PAR DÉFAUT.** `--appliquer` écrit ; sans lui, rien ne bouge et le script
rend ce qu'il ferait. Il vise 293 documents et 626 occurrences en production : un
remplacement de masse sur des contenus de clients ne se lance pas par accident.

## Pourquoi ce script existe

Le renommage du concept « tableau du datastore » a basculé le code, les outils et les
routes. Les **textes** stockés en base — procédures d'org, guides, pages de KB,
déclarations de flotte — emploient encore l'ancien mot. Ils sont servis aux agents :
un agent qui lit `data_write(namespace="slot:vivier")` dans sa procédure fera cet appel,
et recevra un 400 après la bascule.

⚠️ **Et ils ne se corrigent pas AVANT la bascule** : ils enseigneraient alors un
paramètre qui n'existe pas encore. La fenêtre est étroite et elle est après le tag.

## Le mot a CINQ sens ; un seul bascule

    1. le TABLEAU du datastore          ← le seul renommé
    2. la famille d'un outil MCP        (`fr_`, `apollo_`…) — jamais
    3. les données entreprise France    — jamais
    4. le paquet Python `oto.tools`     — jamais
    5. PostgreSQL / `types.SimpleNamespace` — jamais

Sur 626 occurrences : **580 sens 1**, 44 sens 2, 1 sens 3, 1 sens 4. Le classement a été
fait par lecture, pas par motif — un classifieur automatique s'était trompé quatre fois,
dans les deux sens.

## ⚠️ Ce que ce script NE fait PAS, et c'est délibéré

**Il ne se fie à aucun offset figé.** Le rapport de classement en donnait ; les utiliser
serait recopier une position qui bougera dès qu'un document est édité — et ces documents
vivent : une procédure avait déjà disparu entre deux lectures pendant l'inventaire. Le
script **reclasse au moment où il tourne**, et ne porte que les listes d'EXCLUSION, qui
sont des faits stables sur des documents identifiés.
"""
from __future__ import annotations

import argparse
import datetime
import json
import pathlib
import re
import sys

RX = re.compile(r"namespace", re.I)

#: Documents ENTIÈREMENT sens 2 — le mot y désigne la famille d'un outil, jamais un
#: tableau. Aucun remplacement, même partiel.
EXCLUS_ENTIERS: set[tuple[str, str]] = {
    ("blocks", "74618"), ("blocks", "74619"), ("blocks", "74622"), ("blocks", "74625"),
    ("nodes", "31944573"),
}

#: Documents MIXTES : ils portent les DEUX sens dans le même corps. ⚠️ Un remplacement
#: global y renommerait une capacité au lieu d'un tableau. On ne les touche qu'occurrence
#: par occurrence, en excluant les extraits ci-dessous — cherchés par leur TEXTE, jamais
#: par leur position.
GARDE_SENS_2: tuple[str, ...] = (
    # — le catalogue d'outils MCP —
    "namespaceIndex",                              # symbole de code du front
    "namespace de connecteur",
    "connector namespace",
    "connector namespace map",
    "catalogue de namespaces",
    "is a namespace, not a tool",
    "**prefix** matched a namespace",              # ⚠️ le gras coupe la chaîne
    "go beyond the namespace it matched",
    "spelled exactly like a namespace",
    "never mentioned that namespace at all",
    "whose namespace the registry never spoke for",
    "one of its namespaces",
    "unvalidated namespace-match bug",
    "namespace carries a SEND VERB",
    "namespace carries a send verb",
    "`linkedin_unipile_*` namespace",              # ⚠️ les backticks aussi
    "`linear_*` namespace covers issues",
    "`linear_*` namespace is catalogued as",
    "namespace stays the first token of the TOOL name",
    "namespaces `ccn`, `loi`, `juris`",
    "`grain_*` namespace",
    "connecteur à namespaces vides",
    # — les données entreprise France —
    "Droit open data FOD (namespace fr)",
    # — le paquet Python —
    "namespace `oto.tools`",
)


CIBLES = [("org_instructions", "id", ("body_md", "description")),
          ("docs", "id", ("body_md",)),
          ("projects", "id", ("brief_md",)), ("blocks", "id", ("props",)),
          ("nodes", "id", ("props",)), ("runner_fleets", "id", ("input",)),
          ("doctrine_library", "id", ("body_md",)), ("guide_library", "id", ("body_md",)),
          ("platform_instructions", "key", ("body_md",)), ("project_links", "id", ("role",))]


def _remplacer(texte: str) -> tuple[str, int, int]:
    """Rend (texte réécrit, remplacées, préservées). Respecte la casse initiale."""
    faits = gardes = 0
    out: list[str] = []
    i = 0
    for m in RX.finditer(texte):
        # ⚠️ Fenêtre BORNÉE, et la borne est mesurée. À ±1000 caractères,
        # `catalogue de namespaces` attrape par ricochet deux occurrences SENS 1
        # situées ~860 caractères plus loin dans le même corps — une garde trop
        # large ne protège plus, elle censure. Testée sûre de ±120 à ±600.
        fenetre = texte[max(0, m.start() - 250):m.start() + 250]
        if any(g.lower() in fenetre.lower() for g in GARDE_SENS_2):
            gardes += 1
            continue
        mot = m.group(0)
        neuf = ("Datastore" if mot[0].isupper() else "datastore")
        if mot.isupper():
            neuf = "DATASTORE"
        out.append(texte[i:m.start()]); out.append(neuf)
        i = m.end(); faits += 1
    out.append(texte[i:])
    return "".join(out), faits, gardes


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--appliquer", action="store_true",
                    help="ÉCRIT en base. Sans lui : à blanc, rien n'est modifié.")
    ap.add_argument("--sauvegarde", default="/opt/oto-mcp/sauvegardes",
                    help="Où écrire l'état AVANT. La sauvegarde est faite dans le "
                         "MÊME geste que l'écriture — jamais une étape séparée qu'on "
                         "peut oublier de lancer.")
    args = ap.parse_args(argv[1:])

    from oto_mcp.db._conn import _connect

    total = faits = gardes = exclus = docs = 0
    avant: list[dict] = []
    with _connect() as c, c.cursor() as cur:
        for table, pk, cols in CIBLES:
            for col in cols:
                cur.execute(
                    f"SELECT {pk} AS pk, {col}::text AS txt FROM {table} "
                    f"WHERE {col}::text ILIKE %s", ("%namespace%",))
                for r in cur.fetchall():
                    texte = r["txt"] or ""
                    total += len(RX.findall(texte))
                    if (table, str(r["pk"])) in EXCLUS_ENTIERS:
                        exclus += len(RX.findall(texte))
                        continue
                    neuf, n, g = _remplacer(texte)
                    faits += n
                    gardes += g
                    if n:
                        docs += 1
                        # ⚠️ On garde l'état d'AVANT, pas un diff : le remplacement
                        # inverse ne serait PAS exact — 44 occurrences portent déjà
                        # le mot `datastore` légitimement (`datastore_namespace`,
                        # `target=datastore`). Rejouer `datastore` → `namespace`
                        # les casserait. Seul le texte original permet de revenir.
                        avant.append({"table": table, "col": col,
                                      "pk": str(r["pk"]), "texte": texte})
                        avant[-1]["neuf"] = neuf
                        avant[-1]["pkcol"] = pk

    # ⚠️ **La sauvegarde s'écrit AVANT le premier UPDATE, et le script est en DEUX
    # TEMPS pour ça.** Une sauvegarde faite après la boucle ne protège de rien : un
    # plantage au milieu laisserait des documents modifiés sans état d'avant. La
    # première passe ne fait que lire et calculer ; rien n'a encore bougé ici.
    if args.appliquer and avant:
        rep = pathlib.Path(args.sauvegarde)
        rep.mkdir(parents=True, exist_ok=True)
        quand = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        f = rep / f"contenus-namespace-{quand}.json"
        f.write_text(json.dumps(avant, ensure_ascii=False))
        print(f"  ⤷ sauvegarde AVANT : {f} ({len(avant)} documents)")
        if not f.exists() or f.stat().st_size == 0:
            print("  ⛔ sauvegarde ILLISIBLE — rien n'est écrit en base.")
            return 2
        ecrits = 0
        with _connect() as c2, c2.cursor() as cur2:
            for d in avant:
                cur2.execute(
                    f'UPDATE {d["table"]} SET {d["col"]} = %s WHERE {d["pkcol"]} = %s',
                    (d["neuf"], d["pk"]))
                ecrits += 1
        print(f"  ⤷ documents écrits : {ecrits}")

    mode = "APPLIQUÉ" if args.appliquer else "À BLANC — rien n'a été écrit"
    print(f"=== {mode} ===")
    print(f"  occurrences totales        : {total}")
    print(f"  RÉÉCRITES (sens 1)         : {faits}  sur {docs} documents")
    print(f"  préservées (sens 2 en ligne): {gardes}")
    print(f"  préservées (docs exclus)   : {exclus}")
    reste = total - faits - gardes - exclus
    print(f"  non comptées               : {reste}")
    if not args.appliquer:
        print("\n  Pour écrire : --appliquer. ⚠️ Seulement APRÈS le tag de production.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
