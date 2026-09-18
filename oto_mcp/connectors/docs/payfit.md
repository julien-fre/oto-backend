## prerequisite — ta clé api payfit

connecte-toi à PayFit **en admin de l'entreprise**, puis **Intégrations → API** ([app.payfit.com/integrations/hub/api](https://app.payfit.com/integrations/hub/api)) → « Créer une clé » : donne-lui un libellé explicite et coche les scopes, puis copie-la — elle n'est plus affichée ensuite. colle-la dans tes clés de connecteur oto sous `payfit`.
- **la clé décide de ce que tu verras** : oto sert tout ce que l'API expose, mais un champ qu'un scope absent ne renvoie pas n'existe pour personne. pour un pilotage RH et financier complet : `collaborators:read`, `collaborators:management:read`, `collaborators:contracts:read`, `collaborators:personal:read`, `collaborators:legal-identity:read`, `contracts:read`, `contracts:payslips:read`, `time:read`, `accounting:read`, `health-insurance:read`, `collaborators:meal-vouchers:read`
- scopes **sensibles**, à ne cocher que si tu en as l'usage : `collaborators:social-security:read` (NIR), `collaborators:bank-info:read` (IBAN), `payment-files:read` (fichier de virement)
- scopes d'**écriture**, seulement si tu comptes écrire : `collaborators:write`, `collaborators:contracts:write`, `time:write`, `health-insurance:write`
- la clé n'ouvre **que ton entreprise** ; oto retrouve son identifiant tout seul (par introspection), tu n'as aucun identifiant à saisir
- que l'accès API soit inclus ou payant selon l'offre PayFit n'est pas documenté publiquement ; l'accès partenaire (OAuth) est une autre voie, sur candidature
- BYO seulement : pas de clé oto partagée

## setup — un groupe de sociétés, une clé par société

une clé PayFit n'ouvre **qu'une entreprise**, et l'API n'a aucune vue de groupe : deux sociétés = deux clés indépendantes. crée une clé dans chaque entreprise PayFit, puis pose-les comme autant de **comptes nommés** du connecteur `payfit` (un nom par société).
- le premier compte n'a pas besoin de nom ; à partir du deuxième, chacun porte le sien
- viser une société à l'appel : `_account="<nom>"` sur l'outil (`oto_identity(op='list')` pour les lister)
- en fixer une par défaut : `oto_identity(op='set', connector='payfit', identity_id='<nom>')`
- avec une seule clé posée, rien à préciser — elle est servie automatiquement
- ⚠️ **la consolidation se fait chez toi, pas chez PayFit** : pour un chiffre de groupe, appelle société par société et additionne. aucun outil ne parcourt les sociétés tout seul — un total rendu sans avoir bouclé sur chaque `_account` serait le chiffre d'une seule société présenté comme celui du groupe

## usage — de l'annuaire au pilotage financier

commence par `payfit_company()` : son `country` dit si les variantes françaises s'appliquent (contrats FR, titres-restaurant, temps de travail, mutuelle).
- « qui travaille chez nous, qui est son manager ? » → `payfit_collaborator()` (page suivante : `cursor=<next_cursor>`)
- « quel poste, quel type de contrat, quelle convention, forfait jours ? » → `payfit_contract(fr=True)` (`natureContratDsn` : 01 CDI, 02 CDD… ; `workingTimeModality` : `forfait_jours`…)
- « qui est absent la semaine prochaine ? » → `payfit_absence(begin_date="AAAA-MM-JJ", end_date="AAAA-MM-JJ")`, puis relie `contractId` aux `contracts` d'un collaborateur
- « combien nous a coûté la paie de janvier, et en quoi ? » → `payfit_payroll(op="accounting", date="202601")` : une ligne par écriture, avec compte, libellé, débit, crédit, salarié et codes analytiques. **c'est la seule donnée chiffrée structurée de l'API** — masse salariale, charges et avantages en nature s'y lisent par numéro de compte (641x, 645x, 6417x)
- « le mois est-il clos ? » → `payfit_payroll(date="202601")` (op `status` par défaut) **avant** d'exploiter des chiffres
- « le journal pour mon cabinet » → `payfit_payroll(op="accounting_export", date="202601")` ; « le fichier de virement » → `op="payment_file"` — **documents verrouillés par défaut**, cf. la note sur les données personnelles
- « les bulletins de quelqu'un » → `payfit_payslip(collaborator_id=…)` pour la liste, puis `op="download"` avec les trois identifiants de la ligne (le PDF est **verrouillé par défaut**, comme tout document)
- « combien d'heures réalisées ? » → `payfit_worked_time(date="202601")` · « titres-restaurant » → `payfit_meal_voucher(date="202601")`
- « mutuelle et prévoyance » → `payfit_insurance()` pour les contrats de l'entreprise, `kind="provident"` pour la prévoyance
- **le mois s'écrit `AAAAMM`** (`202601`), jamais `2026-01` : c'est la seule forme que PayFit accepte

## note — écrire dans payfit

quatre écritures existent, et toutes partent en **`dry_run=True` par défaut** : l'appel te rend ce qu'il enverrait, sans rien écrire. il faut passer `dry_run=False` délibérément.
- `payfit_collaborator(op="create", …)` crée une **vraie personne** ; `invite_collaborator=True` lui **envoie un e-mail**
- `payfit_contract(op="create", …)` la met **en paie**
- `payfit_absence(op="create", …)` enregistre une absence **déjà validée** — cette API n'a aucun circuit d'approbation, ce que tu écris part en paie ; `op="cancel"` l'annule
- `payfit_insurance(op="affiliate", …)` **remplace** l'affiliation du contrat : un id omis désaffilie. lis l'état courant (`payfit_contract(op="get", fr=True)`) avant d'écrire. `op="regularize"` recalcule des cotisations **déjà passées en paie**

## note — ce que l'api payfit n'a pas

ces questions reviennent souvent et n'ont **aucun endpoint** — oto ne les fabriquera pas, et une réponse inventée serait fausse :
- **les lignes d'un bulletin** (brut, net, cotisation par cotisation) : seuls le PDF et ses métadonnées existent. les seuls montants lisibles par un programme sont les écritures comptables
- **les cumuls annuels**, un « coût employeur » agrégé, les charges en tant que ressource : à reconstituer depuis les écritures, mois par mois
- **la DSN** : les contrats FR portent des champs *codés selon* la norme DSN (nature, statut, IDCC, motif de rupture), mais aucun dépôt ni récupération de DSN
- **les plannings et les pointages** : seul un agrégat mensuel par contrat existe (`payfit_worked_time`)
- **les soldes et compteurs de congés** (CP acquis/pris, RTT restants) : rien. ne les déduis pas d'une liste d'absences
- **l'historique des avenants** et toute modification d'un contrat existant : un contrat se lit tel qu'il est aujourd'hui, et seule sa création s'écrit
- **les notes de frais**, les avantages en nature en tant qu'objet
- **les documents fiscaux** : ceux qui existent (`payfit_document`) sont **britanniques** ; sur une entreprise française la liste est vide, et c'est la bonne réponse

## note — données personnelles : ce qui est masqué, et comment le lever

le connecteur ne retire plus rien en dur : il sert ce que la clé autorise, et la protection est une **politique d'org**, modifiable.
- **masqué par défaut** (défaut serveur) : NIR (et NTT), IBAN/BIC, et le motif d'une absence (`absence_type`) — maladie, accident du travail, maternité sont des données de santé. un `••••` est une valeur masquée, pas une donnée absente
- `absence_category` reste toujours lisible : `ordinary_leave` (congés payés, RTT, repos, sans solde, télétravail, école) ou `restricted` pour tout le reste — de quoi planifier une charge sans lire un motif
- **servi sans masque** : rémunérations des écritures comptables, bulletins, coût employeur, charges, contrats complets (rupture, essai, convention, statut), temps de travail, mutuelle et prévoyance, titres-restaurant, e-mail, téléphone, adresse, date de naissance, sexe, nationalité, ancienneté, manager
- **lever un masque** : un administrateur de l'org pose la politique du connecteur `payfit` (dashboard → carte du connecteur → transformations, ou `oto_org_settings domain=field_filters op=set service=payfit rules=[]`). la politique d'org est autoritaire : `rules: []` lève tout. ⚠️ *effacer* la politique (`rules: null`) fait l'inverse — ça **remet** le défaut serveur
- **les documents sont verrouillés** : un filtre de champs ne voit pas l'intérieur d'un fichier, or le bulletin PDF porte le NIR, le fichier de virement l'IBAN de chaque salarié, l'export comptable les noms et montants par personne (et les documents fiscaux britanniques le numéro d'assurance). ces quatre documents ne sont servis **que si la politique de l'org pour `payfit` ne masque rien** — c'est-à-dire après qu'un administrateur a levé les masques (`rules: []`). sinon l'appel est refusé en le disant, et le refus n'est pas un bug. si la politique ne peut pas être lue, le document est refusé aussi
- les **données** (écritures comptables en JSON, état de la paie, liste des bulletins) ne sont jamais verrouillées : la politique les filtre champ par champ
- dérivé de la documentation et de la spec OpenAPI publiques (lues le 17/09/2026), jamais exercé avec une vraie clé
