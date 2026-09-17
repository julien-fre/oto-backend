## prerequisite — ta clé api payfit

connecte-toi à PayFit **en admin de l'entreprise**, puis **Intégrations → API** ([app.payfit.com/integrations/hub/api](https://app.payfit.com/integrations/hub/api)) → « Créer une clé » : donne-lui un libellé explicite et coche les scopes, puis copie-la — elle n'est plus affichée ensuite. colle-la dans tes clés de connecteur oto sous `payfit`.
- scopes utiles, et rien de plus : `collaborators:read`, `collaborators:management:read` (manager, équipe), `collaborators:contracts:read`, `contracts:read`, `time:read` (absences). **ne coche pas** `collaborators:social-security:read`, `collaborators:bank-info:read`, `collaborators:personal:read`, `collaborators:legal-identity:read` ni `contracts:payslips:read` : le connecteur ne les servirait pas, autant que la clé ne les porte pas
- la clé n'ouvre **que ton entreprise** ; oto retrouve son identifiant tout seul
- que l'accès API soit inclus ou payant selon l'offre PayFit n'est pas documenté publiquement ; l'accès partenaire (OAuth) est une autre voie, sur candidature
- BYO seulement : pas de clé oto partagée

## usage — annuaire, contrats, absences

commence par `payfit_company()` : son `country` dit si la variante FR des contrats s'applique.
- « qui travaille chez nous, qui est son manager ? » → `payfit_collaborator()` (page suivante : `cursor=<next_cursor>`)
- « quel poste, quel type de contrat ? » → `payfit_contract(fr=True)` si l'entreprise est française (`natureContratDsn` : 01 CDI, 02 CDD…)
- « qui est absent la semaine prochaine ? » → `payfit_absence(begin_date="AAAA-MM-JJ", end_date="AAAA-MM-JJ")`, puis relie `contractId` aux `contracts` d'un collaborateur

## note — données personnelles : ce qui n'est pas servi

- **ni paie ni rémunération** : bulletins, journal comptable, fichiers de virement, mutuelle et prévoyance, titres-restaurant restent hors du connecteur
- retirés même si la clé les porte, **sans option pour obtenir le brut** : NIR, IBAN/BIC, date et lieu de naissance, nationalité, sexe, adresses, téléphones et e-mails personnels, temps de travail, motif de rupture de contrat
- **le motif d'une absence** n'est servi que pour un congé ordinaire (congés payés, RTT, repos, sans solde, télétravail, école) ; maladie, accident du travail, maternité, enfant malade, deuil et tout autre type sortent en `absence`
- lecture seule : aucune création ni annulation ; dérivé de la documentation publique, jamais exercé avec une vraie clé
