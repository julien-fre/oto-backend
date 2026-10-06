"""La transformation d'une ligne source en ligne CIBLE — une seule fonction, deux usages.

L'export l'applique pour calculer l'AAD sous laquelle il rechiffre un secret (l'AAD
d'un credential de compte ou de membre contient le sub, qui change) ; l'import
l'applique pour écrire la ligne. Deux copies divergeraient en silence : le secret se
rechiffrerait sous une identité que l'import n'écrit pas, et deviendrait illisible.

Ce qu'elle change, et rien d'autre :
- **les comptes** perdent le préfixe `<slug>:` que leur donnait un tenant tiers
  (`Perimetre.comptes_cible`) : toute VALEUR exactement égale à un sub du périmètre,
  ou à sa forme membre `<org>:<sub>`, à toute profondeur d'un JSON ;
- **le tenant** devient la ligne 1 de la cible : sa ligne prend l'id 1, toute clé
  étrangère vers `tenants(id)` vaut 1, et sa désactivation éventuelle ne le suit pas ;
- **les URL de notre stockage public** (décision du 28/09/2026) : dans toute valeur
  texte, colonne ou contenu (page, JSON), `<base source>/` devient `<base cible>/` ; les
  objets, eux, gardent leur clé (`objets`). Seul l'import connaît la base cible, que la
  cible déclare. L'export s'en passe sans rien changer à ce qu'il calcule, puisqu'aucun
  champ d'AAD (`rechiffrement.AAD`) n'est une URL.
"""
from __future__ import annotations

from dataclasses import dataclass

from .decouverte import Schema


@dataclass(frozen=True)
class Transformation:
    comptes: dict[str, str]                    # sub source → sub cible, identités retirées
    vers_tenant: dict[str, tuple[str, ...]]    # table → colonnes de clé vers `tenants(id)`
    bases: tuple[str, str] | None = None       # (base publique source, base cible)

    @classmethod
    def depuis(cls, schema: Schema, comptes: dict[str, str],
               bases: tuple[str, str] | None = None) -> "Transformation":
        return cls({s: c for s, c in comptes.items() if s != c},
                   {k.table: k.colonnes for k in schema.cles
                    if k.cible == "tenants" and k.colonnes_cible == ("id",)},
                   None if bases is None or bases[0] == bases[1] else bases)

    def _valeur(self, v):
        if isinstance(v, str):
            if v in self.comptes:
                return self.comptes[v]
            tete, sep, reste = v.partition(":")
            if sep and tete.isdigit() and reste in self.comptes:
                return f"{tete}:{self.comptes[reste]}"
            if self.bases and f"{self.bases[0]}/" in v:
                return v.replace(f"{self.bases[0]}/", f"{self.bases[1]}/")
            return v
        if isinstance(v, list):
            return [self._valeur(x) for x in v]
        if isinstance(v, dict):
            return {k: self._valeur(x) for k, x in v.items()}
        return v

    def appliquer(self, table: str, ligne: dict) -> dict:
        cible = self._valeur(ligne) if (self.comptes or self.bases) else dict(ligne)
        for c in self.vers_tenant.get(table, ()):
            if cible[c] is not None:
                cible[c] = 1
        if table == "tenants":
            cible["id"] = 1
            # Une instance ne naît pas désactivée (#1165) : un tenant désactivé ICI au
            # moment de son départ vers sa propre instance y devient le primaire, que
            # rien ne désactive ni ne réactive.
            for c in ("disabled_at", "disabled_by", "disabled_reason"):
                if c in cible:
                    cible[c] = None
        return cible
