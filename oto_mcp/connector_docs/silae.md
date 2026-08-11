## prerequisite — tes accès api silae paie

silae paie v1 utilise des identifiants oauth2 à **trois champs** ; chaque cabinet/employeur saisit les siens, sa paie n'est visible que par lui. demande-les à ton contact [silae](https://www.silae.fr) ou via ton espace api.
- `client_id` — identifiant de l'application api
- `client_secret` — secret associé
- `subscription_key` — clé d'abonnement à l'api silae paie
renseigne ces trois champs dans tes clés de connecteur oto sous `silae`

## usage — consulter dossiers, salariés et bulletins

lecture seule de la paie (les coordonnées bancaires sont masquées avant de t'arriver).
- `silae_dossier(op="list")` liste les dossiers de paie accessibles, `silae_dossier(op="current_period")` la période ouverte
- `silae_employee(op="list")` les salariés d'un dossier, `silae_employee` le détail d'un salarié par matricule
- `silae_payslip(op="list")` les bulletins d'une période, puis `silae_payslip(op="header")` / `silae_payslip(op="lines")` / `silae_payslip(op="totals")` pour le détail d'un bulletin
- `silae_variables_to_enter` les variables de paie (EVP) encore à saisir sur un dossier
