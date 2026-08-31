#!/usr/bin/env python3
"""Confronte le contrat épinglé par un front consommateur au spec que NOUS servons.

Pourquoi ici et pas seulement chez lui. Le front de JB (`otomata-tech/oto-frontend`)
épingle un extrait verbatim de notre OpenAPI et le compare au vivant à chaque passage :
quand nous touchons l'une des opérations qu'il consomme, c'est SA branche principale qui
rougit et SA livraison qui se bloque, alors que le changement vient de nous. Ce script
retourne la charge : celui qui casse l'apprend le premier, avant que l'autre ne le
découvre dans sa journée.

Deux niveaux, délibérément distincts — un contrôle qui crie pour tout n'est plus lu :

  ROUGE (sortie 1) — ses appels ne passeront plus : une opération épinglée a disparu, ou
  sa signature d'ENTRÉE a changé (paramètres, corps de requête, identifiant d'opération).
  Cela demande une décision : garder la compatibilité, ou prévenir avant de livrer.

  AVERTISSEMENT (sortie 0) — le reste des écarts, typiquement une réponse enrichie ou une
  description retouchée. Ses appels continuent de passer, mais SON contrôle à lui, qui est
  exact, rougira à sa prochaine poussée : il devra ré-extraire. Ce n'est pas notre faute à
  réparer, c'est une information à lui transmettre.

  Sortie 2 — l'un des deux documents est illisible. On n'a rien jugé, et il faut le dire :
  confondre « pas de dérive » et « pas de mesure » est la façon habituelle de devenir
  aveugle.

Usage : contrat-front.py <contrat-épinglé.json> <url-ou-fichier-du-spec-servi>
"""
import json
import sys
import urllib.request

ENTREE = ("operationId", "parameters", "requestBody")


def charger(source: str) -> dict:
    """Lit un document OpenAPI, depuis un fichier ou une URL. Lève en cas d'illisible."""
    if source.startswith(("http://", "https://")):
        with urllib.request.urlopen(source, timeout=30) as r:
            brut = r.read()
    else:
        brut = open(source, "rb").read()
    doc = json.loads(brut)
    if not isinstance(doc, dict) or "paths" not in doc:
        raise ValueError(f"{source} : ce n'est pas un document OpenAPI (pas de « paths »)")
    return doc


def signature_entree(op: dict) -> str:
    """Ce qui doit rester stable pour que les appels du front continuent de passer."""
    return json.dumps({k: op.get(k) for k in ENTREE}, sort_keys=True, ensure_ascii=False)


def main() -> int:
    if len(sys.argv) != 3:
        print(__doc__.strip().splitlines()[-1], file=sys.stderr)
        return 2
    try:
        epingle = charger(sys.argv[1])
        servi = charger(sys.argv[2])
    except Exception as exc:                      # noqa: BLE001 — tout illisible = code 2
        print(f"CONFRONTATION IMPOSSIBLE : {exc}", file=sys.stderr)
        return 2

    servis = servi.get("paths", {})
    disparues, entree_changee, forme_changee = [], [], []

    for chemin, operations in epingle.get("paths", {}).items():
        for methode, op in operations.items():
            if not isinstance(op, dict):
                continue
            vivante = (servis.get(chemin) or {}).get(methode)
            nom = f"{methode.upper()} {chemin}"
            if vivante is None:
                disparues.append(nom)
            elif signature_entree(op) != signature_entree(vivante):
                entree_changee.append(nom)
            elif json.dumps(op, sort_keys=True) != json.dumps(vivante, sort_keys=True):
                forme_changee.append(nom)

    total = sum(len(o) for o in epingle.get("paths", {}).values())
    print(f"{total} opération(s) épinglée(s) par le front, confrontées au spec servi.")

    if disparues or entree_changee:
        if disparues:
            print("\nOPÉRATIONS DISPARUES du spec servi :")
            for n in disparues:
                print(f"  - {n}")
        if entree_changee:
            print("\nSIGNATURE D'ENTRÉE MODIFIÉE (paramètres / corps de requête) :")
            for n in entree_changee:
                print(f"  - {n}")
        print(
            "\nCes changements CASSENT les appels du front consommateur. Deux issues, et "
            "c'est une décision, pas une réparation mécanique : garder la compatibilité "
            "(chemin conservé, paramètre optionnel), ou prévenir le front AVANT la mise en "
            "production pour qu'il s'adapte."
        )
        return 1

    if forme_changee:
        print("\nÉcarts SANS effet sur ses appels (réponses ou descriptions retouchées) :")
        for n in forme_changee:
            print(f"  - {n}")
        print(
            "\nRien à réparer ici. Mais le contrôle du front est exact : sa branche "
            "principale rougira à sa prochaine poussée tant qu'il n'a pas ré-extrait son "
            "contrat. Le prévenir évite de lui faire chercher une panne qui n'existe pas."
        )
        return 0

    print("Aucun écart.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
