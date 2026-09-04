#!/usr/bin/env python3
"""Confronte le contrat épinglé par un front consommateur au spec que NOUS servons.

Pourquoi ici et pas seulement chez lui. Le front de JB (`otomata-tech/oto-frontend`)
épingle un extrait verbatim de notre OpenAPI et le compare au vivant à chaque passage :
quand nous touchons l'une des opérations qu'il consomme, c'est SA branche principale qui
rougit et SA livraison qui se bloque, alors que le changement vient de nous. Ce script
retourne la charge : celui qui casse l'apprend le premier, avant que l'autre ne le
découvre dans sa journée.

Deux niveaux, délibérément distincts — un contrôle qui crie pour tout n'est plus lu :

  ROUGE (sortie 1) — ses appels ne passeront plus, sans ambiguïté : opération disparue,
  identifiant d'opération changé, paramètre qu'il envoie disparu / devenu obligatoire /
  changé de type, paramètre obligatoire apparu, corps de requête disparu ou devenu
  obligatoire. Cela demande une décision : garder la compatibilité, ou prévenir avant de
  livrer.

  ⚠️ Ce qui est AJOUTÉ et facultatif n'est PAS une casse. Premier passage réel, le
  31/08/2026 : la branche principale ajoutait un paramètre de requête `reveal` facultatif,
  et ce script a rougi — un contrôle qui crie pour un ajout compatible est exactement ce
  qu'on cesse de lire. Comparer les signatures d'entrée en bloc était trop grossier ; la
  règle est désormais énumérée, et seul l'irréversible pour l'appelant est rouge.

  AVERTISSEMENT (sortie 0) — le reste des écarts, typiquement une réponse enrichie ou une
  description retouchée. Ses appels continuent de passer, mais SON contrôle à lui, qui est
  exact, rougira à sa prochaine poussée : il devra ré-extraire. Ce n'est pas notre faute à
  réparer, c'est une information à lui transmettre.

  Sortie 2 — l'un des deux documents est illisible. On n'a rien jugé, et il faut le dire :
  confondre « pas de dérive » et « pas de mesure » est la façon habituelle de devenir
  aveugle.

  ⚠️ LES RÉFÉRENCES SONT RÉSOLUES AVANT COMPARAISON, et c'est indispensable. Les opérations
  épinglées portent 178 `$ref` vers `#/components/schemas/…` : comparer les opérations telles
  quelles laisse passer TOUT changement vivant dans un composant partagé — le payload de
  l'opération est identique à l'octet, seul le schéma référencé a bougé. Or hoisster une forme
  en composant dès qu'elle devient réutilisable est le geste NORMAL : l'angle mort couvrait donc
  le cas fréquent, pas un cas tordu. Constaté sur oto-backend#738, où 9 composants ajoutés et
  10 316 octets de plus ne sortaient ni en rouge ni en avertissement.

Usage : contrat-front.py <contrat-épinglé.json> <url-ou-fichier-du-spec-servi>
"""
import json
import sys
import urllib.request



def resoudre(doc: dict, noeud, chaine=()):
    """Remplace les `$ref` internes par ce qu'elles désignent, récursivement.

    Une référence déjà en cours d'expansion dans la même branche est laissée telle quelle :
    les schémas récursifs existent, et les inliner sans fin ferait exploser la comparaison.
    Le marqueur est identique des deux côtés, donc l'égalité reste juste.
    """
    if isinstance(noeud, dict):
        ref = noeud.get("$ref")
        if isinstance(ref, str) and ref.startswith("#/"):
            if ref in chaine:
                return {"$ref_cyclique": ref}
            cible = doc
            for part in ref[2:].split("/"):
                part = part.replace("~1", "/").replace("~0", "~")
                if not isinstance(cible, dict) or part not in cible:
                    return {"$ref_introuvable": ref}
                cible = cible[part]
            fusion = resoudre(doc, cible, chaine + (ref,))
            reste = {k: resoudre(doc, v, chaine) for k, v in noeud.items() if k != "$ref"}
            if isinstance(fusion, dict):
                return {**fusion, **reste}
            return fusion
        return {k: resoudre(doc, v, chaine) for k, v in noeud.items()}
    if isinstance(noeud, list):
        return [resoudre(doc, x, chaine) for x in noeud]
    return noeud


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


def _index(op: dict) -> dict:
    """Les paramètres d'une opération, indexés par (nom, emplacement)."""
    return {(p.get("name"), p.get("in")): p for p in (op.get("parameters") or [])}


def casse_les_appels(epingle: dict, servie: dict) -> list:
    """Ce qui empêchera un appel EXISTANT de passer. Rien d'autre.

    Un ajout facultatif — paramètre optionnel, champ de réponse — laisse tout appel
    existant fonctionner : ce n'est pas une casse, et le traiter comme telle rend le
    contrôle inutilisable (vécu au premier passage réel).
    """
    raisons = []
    if epingle.get("operationId") != servie.get("operationId"):
        raisons.append(
            f"l'identifiant d'opération passe de « {epingle.get('operationId')} » "
            f"à « {servie.get('operationId')} »"
        )

    avant, apres = _index(epingle), _index(servie)
    for (nom, ou), p in avant.items():
        q = apres.get((nom, ou))
        if q is None:
            raisons.append(f"le paramètre « {nom} » ({ou}) a disparu")
            continue
        if q.get("required") and not p.get("required"):
            raisons.append(f"le paramètre « {nom} » ({ou}) devient obligatoire")
        sp, sq = (p.get("schema") or {}), (q.get("schema") or {})
        tp, tq = sp.get("type"), sq.get("type")
        if tp != tq and not _elargi_a_nullable(sp, sq):
            raisons.append(f"le paramètre « {nom} » ({ou}) change de type : {tp} → {tq}")
    for (nom, ou), q in apres.items():
        if (nom, ou) not in avant and q.get("required"):
            raisons.append(f"un paramètre OBLIGATOIRE « {nom} » ({ou}) est apparu")

    corps_avant, corps_apres = epingle.get("requestBody"), servie.get("requestBody")
    if corps_avant and not corps_apres:
        raisons.append("le corps de requête a disparu")
    elif corps_apres and not corps_avant and corps_apres.get("required"):
        raisons.append("un corps de requête OBLIGATOIRE est apparu")
    elif corps_avant and corps_apres and corps_apres.get("required") and not corps_avant.get("required"):
        raisons.append("le corps de requête devient obligatoire")

    # Les champs du corps. Les références étant résolues, un champ rendu obligatoire dans un
    # schéma PARTAGÉ se voit ici — c'est le cas qui échappait entièrement au contrôle avant.
    # Un champ AJOUTÉ facultatif ne casse rien ; un champ devenu obligatoire fait rejeter la
    # charge que le front envoie déjà.
    for type_mime, avant in _schemas_de_corps(corps_avant).items():
        apres = _schemas_de_corps(corps_apres).get(type_mime)
        if apres is None:
            continue
        nouveaux = set(apres.get("required") or []) - set(avant.get("required") or [])
        for champ in sorted(nouveaux):
            raisons.append(f"le champ « {champ} » du corps de requête devient obligatoire")
    return raisons


def _elargi_a_nullable(avant: dict, apres: dict) -> bool:
    """`str` devenu `str | null` — un ÉLARGISSEMENT, jamais une casse (04/09/2026).

    Le contrôle jugeait sur `schema.type`, et un `anyOf: [string, null]` n'en a pas :
    il lisait `str → None` et criait « change de type ». Or aucun appel du front ne
    casse quand un paramètre accepte EN PLUS l'absence de valeur — ce qui casserait,
    c'est le rétrécissement inverse, et il reste rouge.

    ⚠️ **Ce que ce contrôle ne voit toujours pas, et qui compte davantage : le DÉFAUT.**
    Un paramètre qui garde son type mais change de valeur par défaut change ce que le
    front OBTIENT sans rien modifier chez lui. C'est le cas qui a fait rougir ce script
    (`scope` de `me.guide.get` : `"org"` → `None`, la lecture cascade désormais chez
    soi d'abord) — et il a rougi pour la mauvaise raison, en taisant la bonne. D'où
    l'avertissement que `_defaut_change` fait remonter à côté."""
    def _types(sch: dict) -> set:
        if sch.get("type"):
            return {sch["type"]}
        return {v.get("type") for v in (sch.get("anyOf") or sch.get("oneOf") or [])
                if isinstance(v, dict) and v.get("type")}
    ta, tb = _types(avant), _types(apres)
    return bool(ta) and ta <= tb and (tb - ta) <= {"null"} and tb != ta


def _defaut_change(epingle: dict, servie: dict) -> list[str]:
    """Les paramètres dont la valeur par DÉFAUT a bougé — avertissement, pas casse.

    Le front continue d'appeler pareil et reçoit autre chose : ça ne « casse » aucun
    appel au sens du schéma, et c'est pourtant ce qu'un consommateur doit savoir en
    premier. Le contrôle n'en disait rien du tout."""
    avant, apres = _index(epingle), _index(servie)
    out = []
    for (nom, ou), p in avant.items():
        q = apres.get((nom, ou))
        if q is None:
            continue
        da = (p.get("schema") or {}).get("default")
        db = (q.get("schema") or {}).get("default")
        if da != db:
            out.append(f"le paramètre « {nom} » ({ou}) change de DÉFAUT : "
                       f"{da!r} → {db!r} — même appel, autre résultat")
    return out


def _schemas_de_corps(corps) -> dict:
    """Le schéma (déjà résolu) de chaque type de contenu d'un corps de requête."""
    if not isinstance(corps, dict):
        return {}
    return {
        mime: (bloc or {}).get("schema") or {}
        for mime, bloc in (corps.get("content") or {}).items()
    }


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
    defauts_changes: list = []      # avertissement : même appel, autre résultat

    for chemin, operations in epingle.get("paths", {}).items():
        for methode, op in operations.items():
            if not isinstance(op, dict):
                continue
            vivante = (servis.get(chemin) or {}).get(methode)
            nom = f"{methode.upper()} {chemin}"
            if vivante is None:
                disparues.append(nom)
                continue
            op = resoudre(epingle, op)
            vivante = resoudre(servi, vivante)
            if (avis := _defaut_change(op, vivante)):
                defauts_changes.append((nom, avis))
            if (raisons := casse_les_appels(op, vivante)):
                entree_changee.append((nom, raisons))
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
            print("\nAPPELS EXISTANTS CASSÉS :")
            for n, raisons in entree_changee:
                print(f"  - {n}")
                for r in raisons:
                    print(f"      · {r}")
        print(
            "\nCes changements CASSENT les appels du front consommateur. Deux issues, et "
            "c'est une décision, pas une réparation mécanique : garder la compatibilité "
            "(chemin conservé, paramètre optionnel), ou prévenir le front AVANT la mise en "
            "production pour qu'il s'adapte."
        )
        return 1

    if defauts_changes:
        # ⚠️ Avertissement et non échec : le schéma reste compatible, mais le front
        # reçoit autre chose sans avoir rien changé. C'est le fait le plus important
        # que ce contrôle produise, et il ne le voyait pas avant le 04/09/2026.
        print("\n⚠️  MÊME APPEL, AUTRE RÉSULTAT — valeurs par défaut modifiées :")
        for n, avis in defauts_changes:
            print(f"  - {n}")
            for a in avis:
                print(f"      · {a}")
        print("  → prévenir le front : ses appels PASSENT toujours, leur réponse change.")

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
