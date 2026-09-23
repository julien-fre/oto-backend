#!/usr/bin/env python3
"""Canari d'un agent événementiel : un corps FIGÉ, un travail, un verdict.

POURQUOI CE SCRIPT EXISTE.

Un agent déclenché par webhook casse en silence. Trois travaux d'un même agent de
production sont morts les 18 et 22/09/2026 — neuf tentatives — sans que rien ne
le dise : la file était vide, l'agent « actif », le dernier déroulé vert. La
panne s'est découverte quatre jours plus tard, en regardant pour autre chose.

Ce que ce canari rend mécanique : après un déploiement de backend ou de worker,
on ENVOIE un événement connu et on regarde ce que la plateforme en fait. Vert, ou
le déploiement n'est pas fini.

⚠️ **Il tape un déclencheur de TEST, jamais celui d'un client.** Un agent de
production écrit chez un tiers (CRM, e-mails) et dépense des crédits : le rejouer
pour se rassurer fabrique de la donnée fausse chez quelqu'un. L'URL et le secret
se passent en argument ou en environnement, et rien ici ne porte de valeur par
défaut — un défaut serait pris un jour où on ne l'attend pas.

⚠️ **Le corps est FIGÉ dans le dépôt** (`--corps`, défaut `tests/canari/evenement.json`).
Un corps improvisé à chaque exécution ne prouve rien : deux exécutions qui ne
portent pas la même charge ne se comparent pas, et le jour où l'une échoue on ne
sait pas si c'est le code ou la charge qui a bougé.

⚠️ **Il ne juge PAS ce que l'agent a produit.** Il dit si le travail est arrivé,
a tourné, et comment il s'est terminé — avec le motif de CHAQUE tentative quand
il a échoué. Ce que vaut le contenu écrit est une autre question, et une question
qu'un canari ne peut pas trancher.

USAGE.

    export OTO_CANARI_HOOK=https://<host>/api/hooks/<id>
    export OTO_CANARI_SECRET=otoh_…
    python scripts/canari_declencheur.py --attendre 600

Sortie 0 = le travail a conclu `done`. 1 = échec, expiration, ou l'attente a
expiré sans conclusion (les trois se distinguent dans le texte, jamais dans le
code de sortie : un script appelant ne doit pas pouvoir confondre « rouge » et
« encore en vol »).
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys
import time
import urllib.error
import urllib.request

#: Le corps par défaut — dans le dépôt, versionné, le même à chaque exécution.
CORPS_PAR_DEFAUT = pathlib.Path(__file__).resolve().parents[1] / "tests" / "canari" / "evenement.json"

#: Entre deux lectures de l'état. Un canari n'est pas une sonde temps réel : il
#: attend un travail qui met des minutes, et interroger toutes les secondes ne le
#: ferait pas arriver plus vite.
_PAUSE_S = 10

#: Ce qu'on considère comme une fin. `expired` en fait partie : un travail périmé
#: n'est pas « encore en vol », c'est un verdict — et le taire ferait attendre le
#: canari jusqu'à sa propre expiration pour rien.
_TERMINAL = {"done", "failed", "expired"}


class Rouge(Exception):
    """Un verdict NÉGATIF, avec sa raison — distinct d'un plantage du canari."""


def _poster(url: str, secret: str, corps: bytes, source: str) -> dict:
    """Envoie l'événement. Rend la réponse de la plateforme.

    ⚠️ `User-Agent` nommé : c'est ce que la livraison garde comme `source`, et
    c'est ce qui permet de reconnaître les livraisons du canari parmi celles
    d'une vraie source sur le même déclencheur.
    """
    req = urllib.request.Request(
        url, data=corps, method="POST",
        headers={"Authorization": f"Bearer {secret}",
                 "Content-Type": "application/json",
                 "User-Agent": source})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        detail = (e.read() or b"").decode("utf-8", "replace")[:400]
        # ⚠️ Un 404 se dit EN TOUTES LETTRES : la route est muette par dessein
        # (elle ne doit pas être un oracle sur les déclencheurs qui existent),
        # donc « introuvable » ici veut dire « mauvais id OU mauvais secret »,
        # et quelqu'un qui lit « 404 » seul cherchera la mauvaise chose.
        if e.code == 404:
            raise Rouge("la porte n'a pas reconnu l'appel (404) — identifiant de "
                        "déclencheur ou secret faux ; la route ne distingue pas "
                        "les deux, par dessein") from e
        raise Rouge(f"la porte a refusé l'événement : HTTP {e.code} {detail}") from e


def _etat(api: str, jeton: str, trigger_id: int, job_id: int) -> dict | None:
    """L'état de LA livraison qu'on vient d'envoyer — retrouvée par le TRAVAIL
    qu'elle a produit, parce que c'est `job_id` que la porte rend (`/api/hooks`
    répond `{ok, job_id, trigger_id, delayed_seconds}`, pas un id de livraison).
    `None` si pas encore là."""
    # ⚠️ Une seule route, op-dispatchée (`POST /api/me/runner/triggers`) — pas de
    # sous-chemin `/deliveries`. Et SANS `with_input` : le canari juge l'issue,
    # il n'a rien à faire du corps qu'il a lui-même envoyé.
    req = urllib.request.Request(
        f"{api}/api/me/runner/triggers", method="POST",
        data=json.dumps({"op": "deliveries", "trigger_id": trigger_id,
                         "limit": 50}).encode(),
        headers={"Authorization": f"Bearer {jeton}",
                 "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        charge = json.loads(r.read() or b"{}")
    for d in charge.get("deliveries") or ():
        if d.get("job_id") == job_id:
            return d
    return None


def _verdict(d: dict) -> None:
    """Lève `Rouge` si la livraison a mal fini. Rend `None` quand tout va bien."""
    statut = d.get("job_status")
    if statut == "done":
        return
    tentatives = d.get("job_attempt_errors") or []
    # ⚠️ TOUTES les tentatives, pas seulement la dernière : trois essais qui
    # échouent différemment ne se réparent pas comme trois essais identiques, et
    # c'est précisément ce que `last_error` seul ne disait pas.
    detail = " · ".join(f"#{t.get('attempt')}: {t.get('error')}" for t in tentatives)
    raise Rouge(f"le travail a fini `{statut}`" + (f" — {detail}" if detail else ""))


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--hook", default=os.environ.get("OTO_CANARI_HOOK"),
                   help="URL complète du déclencheur de TEST (OTO_CANARI_HOOK)")
    p.add_argument("--secret", default=os.environ.get("OTO_CANARI_SECRET"),
                   help="le secret du hook (OTO_CANARI_SECRET)")
    p.add_argument("--jeton", default=os.environ.get("OTO_CANARI_TOKEN"),
                   help="jeton de compte pour LIRE l'état (OTO_CANARI_TOKEN)")
    p.add_argument("--corps", type=pathlib.Path, default=CORPS_PAR_DEFAUT,
                   help="le corps figé à envoyer")
    p.add_argument("--attendre", type=int, default=600,
                   help="secondes d'attente d'une conclusion (0 = poster et sortir)")
    p.add_argument("--source", default="canari-oto",
                   help="ce que la livraison gardera comme source")
    a = p.parse_args()

    manque = [n for n, v in (("--hook", a.hook), ("--secret", a.secret)) if not v]
    if manque:
        # ⚠️ Pas de défaut, et on le dit : un canari qui viserait une valeur par
        # défaut taperait un jour un déclencheur que personne n'avait en tête.
        print(f"canari : il manque {', '.join(manque)} — aucune valeur par défaut, "
              f"volontairement (cf. l'en-tête).", file=sys.stderr)
        return 2
    if not a.corps.is_file():
        print(f"canari : corps introuvable — {a.corps}", file=sys.stderr)
        return 2

    corps = a.corps.read_bytes()
    try:
        recu = _poster(a.hook, a.secret, corps, a.source)
    except Rouge as e:
        print(f"ROUGE — {e}", file=sys.stderr)
        return 1

    job_id = recu.get("job_id")
    print(f"envoyé : travail {job_id} ({len(corps)} octets depuis {a.corps.name}, "
          f"retardé de {recu.get('delayed_seconds') or 0} s)")
    if a.attendre <= 0 or not job_id:
        # ⚠️ Sans `--jeton`, ou sans attente demandée, on s'arrête ICI et on le
        # DIT : « accepté » n'est pas « a tourné ». Rendre 0 en laissant croire
        # au vert serait exactement la panne que ce script existe pour attraper.
        print("accepté par la porte — l'exécution n'est PAS vérifiée "
              "(pas d'attente demandée).")
        return 0
    if not a.jeton:
        print("accepté par la porte — l'exécution n'est PAS vérifiée "
              "(--jeton absent : impossible de relire l'état).")
        return 0

    trigger_id = int(a.hook.rstrip("/").rsplit("/", 1)[-1])
    api = a.hook.split("/api/hooks/")[0]
    fin = time.monotonic() + a.attendre
    while time.monotonic() < fin:
        time.sleep(_PAUSE_S)
        try:
            d = _etat(api, a.jeton, trigger_id, job_id)
        except urllib.error.HTTPError as e:
            print(f"canari : lecture d'état refusée (HTTP {e.code}) — le jeton "
                  f"peut-il lire cette org ?", file=sys.stderr)
            return 2
        if d and d.get("job_status") in _TERMINAL:
            try:
                _verdict(d)
            except Rouge as e:
                print(f"ROUGE — {e}", file=sys.stderr)
                return 1
            print("VERT — le travail a conclu `done`.")
            return 0

    # ⚠️ Ni vert ni rouge : on n'a pas attendu assez, ou personne n'exécute cette
    # org. Les deux méritent un regard, et aucun ne mérite d'être appelé « vert ».
    print(f"ROUGE — aucune conclusion en {a.attendre} s. Soit un worker manque "
          f"(`oto_trigger op=get` dit `runner.armed`), soit l'attente est trop "
          f"courte pour cette procédure.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
