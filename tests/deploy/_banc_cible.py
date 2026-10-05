"""Banc des scripts d'une instance cible (#967) : les exécuter pour de vrai, en root
simulé, dans une racine jetable.

Même principe que `_banc_bleu_vert.py` : les chemins absolus des scripts (`/opt/`,
`/etc/`, `/usr/local/lib/`, `/var/lock/`) sont réécrits vers la racine jetable, et les
commandes qui toucheraient la machine (useradd, uv, git, caddy, systemctl) sont des
doublures qui journalisent. Les chemins DÉRIVÉS de la déclaration sortent de
`declaration.py` : une doublure de `python3` les préfixe au passage, sans rien changer
au code livré.
"""
from __future__ import annotations

import os
import pathlib
import shutil
import stat
import subprocess

ICI = pathlib.Path(__file__).resolve().parent
DEPOT = ICI.parents[1]
DECLARATION = ICI / "declaration_exemple.json"
TRONC = "https://github.com/exemple/tronc.git"
SHA = "0123456789abcdef0123456789abcdef01234567"

DOUBLURES = {
    "id": r'''
if [ "$1" = -u ] && [ -n "${2:-}" ]; then [ -e "$BANC_RACINE/utilisateurs/$2" ] && echo 999 && exit 0; exit 1; fi
echo 0''',
    "useradd": r'''mkdir -p "$BANC_RACINE/utilisateurs"; touch "$BANC_RACINE/utilisateurs/${!#}"''',
    "uv": r'''
if [ "$1" = venv ]; then
  cible="${!#}"; mkdir -p "$cible/bin"
  printf '#!/bin/bash\necho %s\n' "${BANC_PYV:-3.10}" > "$cible/bin/python"; chmod +x "$cible/bin/python"
fi
# `sync --frozen` (oto-backend#932) : comme le vrai, refuse un arbre sans verrou.
if [ "$1" = sync ]; then [ -f uv.lock ] || { echo "uv : pas de uv.lock dans $(pwd)" >&2; exit 2; }; fi''',
    "git": r'''
if [ "$1" = clone ]; then mkdir -p "${!#}/.git"; echo "$3" > "${!#}/.git/origine"; exit 0; fi
if [ "$1" = -C ] && [ "$3" = remote ]; then cat "$2/.git/origine"; exit 0; fi
# `reset --hard <tag>` : l'arbre prend le contenu du tag (ce que le déploiement lit).
if [ "$1" = reset ]; then
  mkdir -p deploy && cp "$BANC_TAG/deploy/lanceur_secrets.py" deploy/ \
    && cp "$BANC_TAG/pyproject.toml" "$BANC_TAG/uv.lock" .
fi
# Le miroir de la porte : le tag existe-t-il, est-il sur le tronc, que contient-il.
case " $* " in
  *" --verify "*) [ -n "${BANC_TAG_INCONNU:-}" ] && exit 1; echo "$BANC_SHA"; exit 0 ;;
  *" merge-base "*) [ -n "${BANC_HORS_TRONC:-}" ] && exit 1; exit 0 ;;
  *" archive "*) exec tar -c -C "$BANC_TAG" deploy oto_mcp pyproject.toml ;;
esac
for a in "$@"; do case "$a" in --short) echo 0123456; exit 0 ;; HEAD) echo "$BANC_SHA"; exit 0 ;; esac; done''',
    "caddy": "exit 0",
    "systemctl": r'''[ "$1" = is-active ] && [ -n "${BANC_DEMARRAGE_KO:-}" ] && exit 3; exit 0''',
    "curl": r'''
url=""; for a in "$@"; do case "$a" in http*) url="$a" ;; esac; done
case "$url" in
  *tls-check*) printf 404 ;;
  http://127.0.0.1:*) : ;;
  https://*) printf 200 ;;
esac''',
    "flock": "exit 0",
    "ss": "exit 0",
    # La garde des migrations (#1163) passe par le lanceur, sous systemd-run : la doublure
    # rend le verdict qu'on lui dicte (`BANC_MIGRATIONS_CODE`, `BANC_MIGRATIONS_DIT`) ;
    # sans dictée, la base est à jour. Le verdict lui-même est jugé à part, sur le script.
    "systemd-run": r'''
case " $* " in
  *" --script deploy/cible/migrations_a_jour.py "*)
    [ -n "${BANC_MIGRATIONS_DIT:-}" ] && echo "$BANC_MIGRATIONS_DIT" >&2
    exit "${BANC_MIGRATIONS_CODE:-0}" ;;
esac
exit 0''',
    "journalctl": "exit 0",
    "sleep": "exit 0",
    "python3": r'''
if [ "${2:-}" = variables ]; then
  /usr/bin/python3 "$@" | sed -e "s#=/#=$BANC_RACINE/#"; exit "${PIPESTATUS[0]}"
fi
if [ "${2:-}" = ecrire ]; then exec /usr/bin/python3 "$1" "$2" "$3" "$4" "$BANC_RACINE"; fi
exec /usr/bin/python3 "$@"''',
}

PREFIXES = ("/opt/", "/etc/", "/usr/local/lib/", "/var/lock/", "/var/lib/", "/run/")


class Banc:
    def __init__(self, racine: pathlib.Path):
        self.racine = racine
        self.tag = racine / "tag"
        self.trace = racine / "trace"
        self.trace.write_text("")
        for rel in ("deploy", "oto_mcp"):
            (self.tag / rel).mkdir(parents=True)
        for f in (DEPOT / "deploy").glob("*"):
            if f.is_file():
                shutil.copy2(f, self.tag / "deploy" / f.name)
        shutil.copytree(DEPOT / "deploy" / "cible", self.tag / "deploy" / "cible")
        for f in ("__init__.py", "env_inventory.py", "env_secrets.py"):
            shutil.copy2(DEPOT / "oto_mcp" / f, self.tag / "oto_mcp" / f)
        for f in ("pyproject.toml", "uv.lock"):
            shutil.copy2(DEPOT / f, self.tag / f)
        for script in (self.tag / "deploy" / "cible").glob("*.sh"):
            texte = script.read_text(encoding="utf-8")
            for p in PREFIXES:
                texte = texte.replace(p, str(racine) + p)
            script.write_text(texte, encoding="utf-8")
        self.bin = racine / "banc-bin"
        self.bin.mkdir()
        for nom, corps in DOUBLURES.items():
            f = self.bin / nom
            f.write_text('#!/bin/bash\necho "' + nom + ' $*" >> "$BANC_TRACE"\n' + corps + "\n")
            f.chmod(0o755)
        (racine / "etc/caddy").mkdir(parents=True)
        (racine / "etc/exemple").mkdir(parents=True, mode=0o700)
        self.cle = racine / "etc/exemple/scw.key"
        self.cle.write_text("cle\n")
        self.cle.chmod(0o600)
        (racine / "etc/systemd/system").mkdir(parents=True)
        (racine / "var/lock").mkdir(parents=True)
        self.caddyfile(avec_import=True)

    def caddyfile(self, avec_import: bool) -> None:
        lignes = ["# Caddyfile du banc"]
        if avec_import:
            lignes += [f"import {self.racine}/etc/caddy/upstream-exemple-{r}.conf"
                       for r in ("preprod", "prod")]
        (self.racine / "etc/caddy/Caddyfile").write_text("\n".join(lignes) + "\n")

    def lancer(self, script: str, *args: str, declaration: pathlib.Path = DECLARATION,
               **env: str) -> subprocess.CompletedProcess:
        environnement = {"PATH": f"{self.bin}:/usr/bin:/bin", "BANC_TRACE": str(self.trace),
                         "BANC_RACINE": str(self.racine), "LANG": "C.UTF-8",
                         "OTO_CIBLE_DEPOT": TRONC, "BANC_TAG": str(self.tag),
                         "BANC_SHA": SHA, **env}
        return subprocess.run(["/bin/bash", str(self.tag / "deploy" / "cible" / script),
                               str(declaration), *args],
                              env=environnement, capture_output=True, text=True, timeout=60)

    def porte(self, commande: str, entree: str | None = None, **env: str
              ) -> subprocess.CompletedProcess:
        """La porte telle que sshd l'appelle : UN argument, la déclaration sur stdin."""
        (self.racine / "run").mkdir(exist_ok=True)
        environnement = {"PATH": f"{self.bin}:/usr/bin:/bin", "BANC_TRACE": str(self.trace),
                         "BANC_RACINE": str(self.racine), "LANG": "C.UTF-8",
                         "BANC_TAG": str(self.tag), "BANC_SHA": SHA, **env}
        return subprocess.run(["/bin/bash", str(self.tag / "deploy" / "cible" / "porte.sh"),
                               commande],
                              input=DECLARATION.read_text() if entree is None else entree,
                              env=environnement, capture_output=True, text=True, timeout=60)

    def commandes(self) -> list[str]:
        return self.trace.read_text().replace(str(self.racine), "").splitlines()

    def lire(self, chemin: str) -> str:
        return (self.racine / chemin.lstrip("/")).read_text(encoding="utf-8")

    def mode(self, chemin: str) -> str:
        return oct(stat.S_IMODE(os.stat(self.racine / chemin.lstrip("/")).st_mode))
