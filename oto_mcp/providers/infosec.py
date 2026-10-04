"""Déclaration de registre du connecteur `infosec`.

Domicile unique de son entrée : `providers/__init__.py` l'AGRÈGE (il ne la
décrit pas). Cf. `providers/_model.py` pour le contrat de `Connector`.
"""
from __future__ import annotations

from ._model import _c

# infosec : recon PASSIF d'un domaine (RDAP/DNS/CT/TLS/headers, OSINT, sans clé).
# Complète fr_* (identité légale) par l'empreinte numérique. Pas de scan intrusif.
CONNECTOR = _c(
    "infosec", ["infosec"], secret_kind="none",
    label="Infosec", help="vérification d'un domaine : délivrabilité e-mail (SPF/DMARC/DKIM, listes noires, note + recommandations), whois/RDAP, DNS, sous-domaines (CT), TLS, headers de sécurité (recon passif)",
)

CATEGORY = "Infosec"
PUBLISHER = "Otomata (OSINT)"
DESCRIPTION = (
    "Vérification d'un domaine, en lecture passive : délivrabilité e-mail "
    "(SPF/DMARC/DKIM, listes noires, note et recommandations), WHOIS/RDAP, DNS, "
    "sous-domaines via Certificate Transparency, TLS et headers de sécurité."
)
SANS_LOGO_DE_MARQUE = True
