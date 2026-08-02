## usage — empreinte numérique d'un domaine

recon **passif** / osint d'un domaine (rien d'intrusif, sources publiques) — open data, sans clé.
- `infosec_whois(domain)` / `infosec_dns(domain)` — immatriculation rdap et enregistrements dns (avec indices de stack mail/saas)
- `infosec_email_security(domain)` — posture spf/dmarc/dkim, signal de maturité it d'un prospect
- `infosec_subdomains(domain)` — sous-domaines connus via les logs certificate transparency (crt.sh)
- `infosec_tls(domain)` / `infosec_headers(domain)` — certificat tls et en-têtes http de sécurité
