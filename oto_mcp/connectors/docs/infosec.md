## usage — vérifier un domaine

lecture **passive** d'un domaine (rien d'intrusif, sources publiques) — open data, sans clé.
un seul outil, `infosec_domain(op=…, domain=…)` — l'op choisit la lecture :
- `deliverability` — bilan de délivrabilité e-mail de SON domaine : note /100, recommandations classées
  (spf/dmarc/dkim + listes noires). c'est le point d'entrée de « nos e-mails tombent-ils en spam ? »
- `blocklist` — le domaine et ses ip d'envoi (`ip`, sinon les `ip4:` du spf) sur les listes noires gratuites
  dont les conditions permettent un usage commercial automatisé (spamcop, psbl, nordspam, s5h) ;
  spamhaus, surbl, uribl, barracuda, mailspike et uceprotect → rendus `not_checked`, jamais « propres »
- `email_security` — spf (nombre de requêtes vs limite de 10), dmarc complet, dkim (+ `dkim_selector`),
  mta-sts, tls-rpt, bimi, mx
- `whois` / `dns` — immatriculation rdap et enregistrements dns (avec indices de stack mail/saas)
- `subdomains` — sous-domaines connus via les logs certificate transparency (crt.sh)
- `tls` / `headers` — certificat tls et en-têtes http de sécurité

⚠️ ce qui n'a pas pu être lu (erreur dns, échéance de 25 s) est nommé dans `coverage` et n'est pas noté :
une erreur sur `_dmarc` n'est pas « pas de dmarc ». le parcours spf s'arrête au-delà de 10 requêtes (rfc 7208).

⚠️ un domaine qui envoie via google, microsoft ou un outil d'emailing n'a pas d'ip à lui : les listes
d'ip n'en disent rien, ce sont les listes de domaines qui comptent (la réponse le dit dans `notes`).

## note — ce que ça sert à qualifier, au-delà de la sécurité

le nom du connecteur dit « sécurité », mais l'usage le plus courant ici est
**commercial** : reconnaître l'outillage d'une cible pour la qualifier avant de
l'aborder. c'est ce que la ligne « indices de stack mail/saas » recouvre sans le dire.

- les enregistrements `dns` (`MX`, `TXT`) nomment le fournisseur de messagerie et
  souvent les saas branchés dessus — savoir qu'un prospect est chez tel hébergeur, tel
  crm ou tel outil de signature dit avec quoi ton offre devra coexister, ou ce qu'elle
  remplacerait ;
- `email_security` (spf/dmarc/dkim) se lit comme un **signal de maturité it** : une
  posture stricte et complète ne décrit pas la même organisation qu'un domaine sans
  dmarc ;
- `subdomains` révèle des produits, des environnements et des marques annexes qu'aucune
  page d'accueil ne montre.

⚠️ tout est **passif** et vient de sources publiques : aucune sollicitation de la
cible, rien d'intrusif. c'est ce qui rend l'usage commercial acceptable — on lit ce que
le domaine publie de lui-même.
