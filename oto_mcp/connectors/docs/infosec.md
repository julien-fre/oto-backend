## usage — empreinte numérique d'un domaine

recon **passif** / osint d'un domaine (rien d'intrusif, sources publiques) — open data, sans clé.
un seul outil, `infosec_domain(domain, aspect=…)` — l'aspect choisit la lecture :
- `whois` / `dns` — immatriculation rdap et enregistrements dns (avec indices de stack mail/saas)
- `email_security` — posture spf/dmarc/dkim, signal de maturité it d'un prospect
- `subdomains` — sous-domaines connus via les logs certificate transparency (crt.sh)
- `tls` / `headers` — certificat tls et en-têtes http de sécurité
