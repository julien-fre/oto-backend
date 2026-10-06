#!/usr/bin/env python3
"""La déclaration d'une instance cible : la valider, en dériver tout le reste (#967).

Une cible se DÉCLARE dans un seul document JSON, tenu dans l'environnement GitHub de la
cible (jamais dans ce dépôt, qui ne porte que sa forme : `declaration.gabarit.json`). Le
workflow de la cible le transmet à chaque déploiement ; ce module est le seul à le lire.

    {
      "instance": "<nom court>",                    # ^[a-z][a-z0-9]{1,15}$
      "secrets": {"region": "<région>", "projet": "<projet Scaleway de la cible>"},
      "roles": {
        "preprod" | "prod": {
          "ports": {"blue": <port>, "green": <port>},
          "hote_public": "<nom d'hôte public du MCP>",
          "ask": <bool : ce rôle porte l'`ask` de l'on-demand TLS>,
          "drain_max": <secondes>,
          "secrets_optionnels": ["<secret facultatif de l'inventaire>", …],
          "env": {"<VARIABLE NON SECRÈTE DE L'INVENTAIRE>": "<valeur>", …}
        }
      }
    }

TOUT le reste se DÉRIVE du nom d'instance et du rôle — utilisateur de service, arbres,
unités, amont Caddy, verrou, vidange — pour qu'aucun chemin écrit par root ne vienne
d'une valeur libre. `variables` l'imprime en affectations shell ; `ecrire` pose les
fichiers d'environnement du rôle. Un document qui s'écarte de la forme est refusé en
entier, en nommant chaque écart : on ne déploie pas une moitié de déclaration.

Validation contre l'inventaire de CE tag (`oto_mcp/env_inventory.py`,
`oto_mcp/env_secrets.py`) : le `.env` d'un rôle ne porte que des variables inventoriées
et NON secrètes, et il porte toutes celles dont l'instance ne peut se passer (identité,
requises non secrètes) — le refus arrive ici, avant le déploiement, plutôt qu'au boot.
Et contre la bibliothèque bleu/vert de CE tag : tant que sa santé (`HEALTH_PATH`) lit un
chemin que seule la façade DCR sert, chaque rôle déclare l'interrupteur de la façade
(`exigees_par_la_sante`) — sans lui, la première montée échouerait sur un 404.
Et contre le relais d'autorisation (`oto_mcp/auth/relay.py`, #1164) : un rôle dont le MCP
sert la façade devant NOTRE annuaire administrable porte l'hôte de son URL publique dans
`OTO_MCP_OAUTH_RELAY_HOSTS` (`hote_a_relayer`) — sans lui, le relais est éteint et les
rafraîchissements des clients qui ont lu sa métadonnée sont refusés.

Pur : bibliothèque standard seulement, exécuté par le Python du système de la cible.
"""
from __future__ import annotations

import json
import re
import shlex
import sys
from pathlib import Path
from urllib.parse import urlparse

ARBRE = Path(__file__).resolve().parents[2]
if str(ARBRE) not in sys.path:
    sys.path.insert(0, str(ARBRE))

from oto_mcp import env_inventory as inv  # noqa: E402 — l'inventaire DU TAG
from oto_mcp import env_secrets  # noqa: E402

ROLES = ("preprod", "prod")
COULEURS = ("blue", "green")
INSTANCE = re.compile(r"^[a-z][a-z0-9]{1,15}$")
HOTE = re.compile(r"^(?=.{1,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")
REGION = re.compile(r"^[a-z]{2}-[a-z]{3}$")
PROJET = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
# Posées par l'unité elle-même : les redéclarer dans le `.env` les écraserait.
PORTEES_PAR_L_UNITE = frozenset({"HOST", "PORT", "MCP_TRANSPORT"})
CLES = {"instance", "secrets", "roles"}
CLES_SECRETS = {"region", "projet"}
CLES_ROLE = {"ports", "hote_public", "ask", "drain_max", "secrets_optionnels", "env"}
# La santé du bleu/vert : le chemin que la bibliothèque DU TAG interroge, en local sur
# la couleur qui démarre puis en public après la bascule (`HEALTH_PATH`).
BIBLIOTHEQUE_BLEU_VERT = ARBRE / "deploy" / "oto-mcp-bluegreen.sh"
# Ce chemin n'est servi QUE par la façade DCR (`oto_mcp/auth/facade.py`), que
# `oto_mcp/server.py` ne monte que si son interrupteur est posé et non vide.
CHEMIN_DE_LA_FACADE = "/.well-known/oauth-authorization-server"
INTERRUPTEUR_DE_LA_FACADE = "OTO_MCP_CLAUDE_APP_ID"
# Le relais d'autorisation (#1164). La façade le consulte, pour NOTRE annuaire, sur l'hôte
# de son URL publique (`facade.make_routes` : `relay.relais_actif(public_host)`), et il
# n'agit que si cet annuaire est administrable — son credential de management présent
# (`facade._credential_present`, `_PRIMARY_CREDENTIAL` + `_ID`/`_SECRET`).
LISTE_DU_RELAIS = "OTO_MCP_OAUTH_RELAY_HOSTS"     # `relay.HOSTS_ENV`
URL_PUBLIQUE = "OTO_MCP_PUBLIC_URL"               # le `public_url` de la façade
CREDENTIAL_DE_L_ANNUAIRE = "OTO_MCP_LOGTO_M2M"    # `facade._PRIMARY_CREDENTIAL`


class Refus(Exception):
    """La déclaration est refusée ; `ecarts` les nomme tous."""

    def __init__(self, ecarts: list[str]):
        super().__init__("\n".join(ecarts))
        self.ecarts = ecarts


def _cles(ecarts: list[str], ou: str, obj, attendues: set[str]) -> bool:
    if not isinstance(obj, dict):
        ecarts.append(f"{ou} : un objet est attendu")
        return False
    for c in sorted(set(obj) - attendues):
        ecarts.append(f"{ou}.{c} : clé inconnue")
    for c in sorted(attendues - set(obj)):
        ecarts.append(f"{ou}.{c} : manquante")
    return True


def _entier(ecarts, ou, v, bas, haut) -> None:
    if not isinstance(v, int) or isinstance(v, bool) or not bas <= v <= haut:
        ecarts.append(f"{ou} : entier entre {bas} et {haut} attendu (reçu {v!r})")


def exigees_dans_env() -> tuple[str, ...]:
    """Ce que le `.env` d'un rôle doit porter : l'identité, et les requises qui ne sont
    pas des secrets (celles-ci arrivent par le lanceur)."""
    return tuple(v.nom for v in inv.NOMS_FIXES
                 if v.classe in (inv.Classe.IDENTITE, inv.Classe.REQUISE)
                 and not env_secrets.est_secret(v.nom))


def chemin_de_sante() -> str:
    """Le chemin dont le 200 rend une couleur saine, lu dans la bibliothèque du tag :
    la règle qui en dépend suit la bibliothèque, elle ne la recopie pas."""
    m = re.search(r'(?m)^HEALTH_PATH="(/[^"]*)"$',
                  BIBLIOTHEQUE_BLEU_VERT.read_text(encoding="utf-8"))
    if not m:
        raise RuntimeError(f"{BIBLIOTHEQUE_BLEU_VERT} ne déclare pas HEALTH_PATH")
    return m.group(1)


def exigees_par_la_sante() -> tuple[str, ...]:
    """Ce que le `.env` d'un rôle doit porter, non vide, pour que sa couleur puisse
    devenir saine : l'interrupteur de la façade DCR, tant que la santé lit son chemin."""
    return (INTERRUPTEUR_DE_LA_FACADE,) if chemin_de_sante() == CHEMIN_DE_LA_FACADE else ()


def hotes_du_relais(valeur: str) -> frozenset:
    """La liste du relais telle que le serveur la lit (`relay.hosts_declares`) : des noms
    d'hôte nus, casse et point final ignorés — un schéma ou un port n'y correspond à rien."""
    return frozenset(h.strip().lower().rstrip(".") for h in valeur.split(",") if h.strip())


def hote_a_relayer(env: dict, optionnels: list) -> str | None:
    """L'hôte que le serveur de ce rôle cherche dans la liste du relais pour le servir —
    ou None si le relais n'y agirait pas : façade non montée, ou annuaire non administrable
    (le relais y répondrait 503). "" si la façade le chercherait sur une URL sans hôte."""
    if not env.get(INTERRUPTEUR_DE_LA_FACADE):
        return None
    if not (env.get(f"{CREDENTIAL_DE_L_ANNUAIRE}_ID")
            and f"{CREDENTIAL_DE_L_ANNUAIRE}_SECRET" in optionnels):
        return None
    url = env.get(URL_PUBLIQUE)
    try:
        hote = urlparse(url.rstrip("/")).hostname if isinstance(url, str) else None
    except ValueError:   # une URL malformée n'a pas d'hôte : le refus le nomme
        hote = None
    return (hote or "").lower().rstrip(".")


def _relais(ecarts: list[str], ou: str, nom: str, env: dict, optionnels: list) -> None:
    hote = hote_a_relayer(env, optionnels)
    if hote is None:
        return
    if not hote:
        ecarts.append(f"{ou}.env.{URL_PUBLIQUE} : aucun nom d'hôte — le relais "
                      "d'autorisation de ce rôle ne peut pas y être cherché")
        return
    liste = env.get(LISTE_DU_RELAIS, "")
    if isinstance(liste, str) and hote in hotes_du_relais(liste):
        return
    ecarts.append(
        f"{ou}.env.{LISTE_DU_RELAIS} : l'hôte public du rôle {nom}, « {hote} » (celui de "
        f"{URL_PUBLIQUE}), n'y figure pas — ce rôle sert la façade OAuth devant un annuaire "
        f"administrable ({CREDENTIAL_DE_L_ANNUAIRE}_ID et _SECRET déclarés) : sans son hôte "
        "dans la liste, le relais d'autorisation est éteint et les rafraîchissements des "
        "clients MCP qui ont lu sa métadonnée sont refusés. La liste se lit en noms d'hôte "
        "nus séparés par des virgules (ni schéma ni port ; casse et point final ignorés)")


def _role(ecarts: list[str], nom: str, r) -> None:
    ou = f"roles.{nom}"
    if not _cles(ecarts, ou, r, CLES_ROLE):
        return
    if _cles(ecarts, f"{ou}.ports", r.get("ports"), set(COULEURS)):
        for c in COULEURS:
            if c in r["ports"]:
                _entier(ecarts, f"{ou}.ports.{c}", r["ports"][c], 1024, 65535)
    if "hote_public" in r and not (isinstance(r["hote_public"], str)
                                   and HOTE.match(r["hote_public"])):
        ecarts.append(f"{ou}.hote_public : nom d'hôte attendu (reçu {r['hote_public']!r})")
    if "ask" in r and not isinstance(r["ask"], bool):
        ecarts.append(f"{ou}.ask : booléen attendu")
    if "drain_max" in r:
        _entier(ecarts, f"{ou}.drain_max", r["drain_max"], 1, 3600)
    optionnels = r.get("secrets_optionnels", [])
    if not isinstance(optionnels, list) or not all(isinstance(n, str) for n in optionnels):
        ecarts.append(f"{ou}.secrets_optionnels : liste de noms attendue")
    else:
        requis = set(env_secrets.secrets_requis())
        for n in optionnels:
            if not env_secrets.est_secret(n) or n in env_secrets.HORS_SERVEUR or n in requis:
                ecarts.append(f"{ou}.secrets_optionnels : {n} n'est pas un secret "
                              "facultatif de l'inventaire")
        if len(set(optionnels)) != len(optionnels):
            ecarts.append(f"{ou}.secrets_optionnels : doublon")
    env = r.get("env", {})
    if not isinstance(env, dict):
        ecarts.append(f"{ou}.env : un objet est attendu")
        return
    for n, v in sorted(env.items()):
        if env_secrets.est_secret(n):
            ecarts.append(f"{ou}.env.{n} : c'est un secret — il va dans le Secret Manager "
                          "de la cible, jamais dans le .env")
        elif n in PORTEES_PAR_L_UNITE:
            ecarts.append(f"{ou}.env.{n} : posée par l'unité, ne pas la déclarer")
        elif not inv.est_couverte(n):
            ecarts.append(f"{ou}.env.{n} : variable absente de l'inventaire")
        if not isinstance(v, str) or any(c in v for c in "\n\r\0"):
            ecarts.append(f"{ou}.env.{n} : chaîne sur une ligne attendue")
    for n in exigees_dans_env():
        if n not in env:
            ecarts.append(f"{ou}.env.{n} : exigée par l'inventaire, manquante")
    for n in exigees_par_la_sante():
        if not env.get(n):
            ecarts.append(
                f"{ou}.env.{n} : exigée non vide par le bleu/vert — sa santé lit "
                f"{CHEMIN_DE_LA_FACADE}, que seule la façade DCR sert, et la façade n'est "
                f"montée que si {n} est posée ; sans elle, la couleur répond 404 et la "
                "montée échoue en « couleur pas devenue saine »")
    if isinstance(optionnels, list):
        _relais(ecarts, ou, nom, env, optionnels)
    # Ce que le process DÉCLARE être (`config.est_la_production`) est le rôle qu'on
    # déploie : une préprod qui se dirait prod agirait sur des tiers avec son code.
    if env.get("OTO_ENV") != nom:
        ecarts.append(f"{ou}.env.OTO_ENV : doit valoir « {nom} », le rôle déployé "
                      f"(reçu {env.get('OTO_ENV')!r})")


def valider(doc) -> dict:
    """Le document tel quel s'il est conforme ; sinon `Refus` avec TOUS les écarts."""
    ecarts: list[str] = []
    if _cles(ecarts, "declaration", doc, CLES):
        i = doc.get("instance")
        if "instance" in doc and not (isinstance(i, str) and INSTANCE.match(i)):
            ecarts.append(f"instance : {INSTANCE.pattern} attendu (reçu {i!r})")
        s = doc.get("secrets")
        if "secrets" in doc and _cles(ecarts, "secrets", s, CLES_SECRETS):
            if "region" in s and not (isinstance(s["region"], str) and REGION.match(s["region"])):
                ecarts.append(f"secrets.region : région attendue (reçu {s['region']!r})")
            if "projet" in s and not (isinstance(s["projet"], str) and PROJET.match(s["projet"])):
                ecarts.append("secrets.projet : identifiant de projet (UUID) attendu")
        roles = doc.get("roles")
        if "roles" in doc:
            if not isinstance(roles, dict) or not roles:
                ecarts.append("roles : au moins un rôle attendu")
            else:
                for nom in sorted(roles):
                    if nom not in ROLES:
                        ecarts.append(f"roles.{nom} : rôle inconnu (attendu {' | '.join(ROLES)})")
                    else:
                        _role(ecarts, nom, roles[nom])
                ports = [p for r in roles.values() if isinstance(r, dict)
                         for p in (r.get("ports") or {}).values()]
                if len(ports) != len(set(ports)):
                    ecarts.append("roles.*.ports : deux couleurs partagent un port")
    if ecarts:
        raise Refus(ecarts)
    return doc


def lire(chemin: str) -> dict:
    try:
        doc = json.loads(Path(chemin).read_text(encoding="utf-8"))
    except (OSError, ValueError) as erreur:
        raise Refus([f"déclaration illisible : {erreur}"]) from erreur
    return valider(doc)


def variables(doc: dict, role: str) -> dict[str, str]:
    """Tout ce que l'amorce et le déploiement d'un rôle utilisent, dérivé."""
    if role not in doc["roles"]:
        raise Refus([f"roles.{role} : la cible ne déclare pas ce rôle"])
    i, r = doc["instance"], doc["roles"][role]
    unite = f"{i}-{role}"
    etc = f"/etc/{i}/{role}"
    return {
        "INSTANCE": i,
        "ROLE": role,
        "UTILISATEUR": f"oto-{i}",
        "UNITE": unite,
        "ETC_INSTANCE": f"/etc/{i}",
        "ETC_ROLE": etc,
        "CLE_SCW": f"/etc/{i}/scw.key",
        "PYTHONS": f"/opt/{i}/python",
        "BG_ENV": role,
        "BG_UNIT": unite,
        "BG_TREE": f"/opt/{i}/{role}",
        "BG_PORT_blue": str(r["ports"]["blue"]),
        "BG_PORT_green": str(r["ports"]["green"]),
        "BG_UPSTREAM": f"/etc/caddy/upstream-{unite}.conf",
        "BG_SNIPPET": f"{i}_{role}",
        "BG_DOCSHARE_HOST": r["hote_public"],
        "BG_ASK": "1" if r["ask"] else "",
        "BG_ACTIVE": f"{etc}/active",
        "BG_PUBLIC": f"https://{r['hote_public']}{chemin_de_sante()}",
        "BG_DRAIN_MAX": str(r["drain_max"]),
        "BG_LOCK": f"/var/lock/{unite}-bleu-vert.lock",
        "BG_CADDYFILE": "/etc/caddy/Caddyfile",
        "BG_DRAIN": f"/usr/local/lib/{i}/oto-mcp-drain.sh",
        "BG_DRAIN_UNIT": f"{unite}-vidange",
        "BG_LANCEUR": "versionne",
    }


def valeur_env(valeur: str) -> str:
    """Une valeur telle que systemd la relira À L'IDENTIQUE dans un `EnvironmentFile`.

    Non citée, systemd retire les guillemets qu'elle contient (un JSON perdrait les
    siens) et les espaces de fin : on cite donc toujours. Entre apostrophes, tout est
    littéral ; une valeur qui contient elle-même une apostrophe passe entre guillemets,
    où seuls `\\` et `\"` s'échappent."""
    if "'" not in valeur:
        return f"'{valeur}'"
    return '"' + valeur.replace("\\", "\\\\").replace('"', '\\"') + '"'


def fichiers(doc: dict, role: str) -> dict[str, str]:
    """Les fichiers d'environnement du rôle, chemin -> contenu."""
    v = variables(doc, role)
    r, s = doc["roles"][role], doc["secrets"]
    entete = "# Écrit par deploy/cible/declaration.py depuis la déclaration de la cible — ne pas éditer.\n"
    app = entete + "".join(f"{n}={valeur_env(val)}\n" for n, val in sorted(r["env"].items()))
    lanceur = entete + (f"OTO_SECRETS_REGION={s['region']}\n"
                        f"OTO_SECRETS_PROJET={s['projet']}\n"
                        f"OTO_SECRETS_CHEMIN=/{role}\n"
                        f"OTO_SECRETS_OPTIONNELS={' '.join(r['secrets_optionnels'])}\n")
    sortie = {f"{v['ETC_ROLE']}/app.env": app, f"{v['ETC_ROLE']}/lanceur.env": lanceur}
    for c in COULEURS:
        sortie[f"{v['ETC_ROLE']}/port-{c}.env"] = entete + f"PORT={r['ports'][c]}\n"
    return sortie


def ecrire(doc: dict, role: str, racine: Path) -> list[str]:
    ecrits = []
    for chemin, contenu in fichiers(doc, role).items():
        cible = racine / chemin.lstrip("/")
        cible.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        tmp = cible.with_name(cible.name + ".nouveau")
        tmp.write_text(contenu, encoding="utf-8")
        tmp.chmod(0o600)
        tmp.replace(cible)
        ecrits.append(chemin)
    return ecrits


USAGE = """usage :
  declaration.py verifier  <déclaration.json>
  declaration.py variables <déclaration.json> <rôle>      affectations shell, dérivées
  declaration.py ecrire    <déclaration.json> <rôle> [racine]   fichiers d'env du rôle"""


def main(argv: list[str]) -> int:
    if len(argv) < 3 or argv[1] not in ("verifier", "variables", "ecrire"):
        print(USAGE, file=sys.stderr)
        return 2
    try:
        doc = lire(argv[2])
        if argv[1] == "verifier":
            # sans le nom d'instance : la CI d'un dépôt public l'affiche (D5)
            print(f"déclaration conforme : rôles {', '.join(sorted(doc['roles']))}")
        elif len(argv) < 4 or argv[3] not in ROLES:
            print(USAGE, file=sys.stderr)
            return 2
        elif argv[1] == "variables":
            for n, val in variables(doc, argv[3]).items():
                print(f"{n}={shlex.quote(val)}")
        else:
            racine = Path(argv[4]) if len(argv) > 4 else Path("/")
            for chemin in ecrire(doc, argv[3], racine):
                print(f"écrit : {chemin}")
    except Refus as refus:
        print("déclaration REFUSÉE :", file=sys.stderr)
        for e in refus.ecarts:
            print(f"  - {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
