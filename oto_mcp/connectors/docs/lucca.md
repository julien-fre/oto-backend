## prerequisite — tes accès api lucca

lucca utilise une clé d'api statique et le sous-domaine de ton instance ; chaque cabinet/employeur saisit les siens, ses données ne sont visibles que par lui. génère la clé depuis ton compte lucca ou via ton contact [lucca](https://www.lucca.fr).
- `api_key` — clé d'api générée dans réglages → api
- `domain` — le sous-domaine seul de ton instance (ex. `acme` pour acme.ilucca.net), pas l'url complète
renseigne ces deux champs dans tes clés de connecteur oto sous `lucca`

## usage — consulter annuaire, absences, frais et organisation

lecture seule.
- `lucca_employee(op="list")` liste les salariés joignables, `lucca_employee(op="get")` le détail d'un salarié par id
- `lucca_absence(op="list")` les absences posées sur une période (`date` obligatoire ; le commentaire libre est coupé par défaut, `fields=["*"]` le rend), `lucca_absence(op="get")` le détail d'une absence
- `lucca_leave_request(op="list")` les demandes de congé (le workflow d'approbation), `lucca_leave_request(op="get")` le détail d'une demande
- `lucca_expense_claim()` les notes de frais — pas de détail par id, lucca n'expose que la liste
- `lucca_department(op="list")` les départements (les listes de salariés sont coupées par défaut, `fields=["*"]` les rend), `lucca_department(op="get")` le détail d'un département
- `lucca_establishment()` les établissements — pas de détail par id, base et pagination différentes du reste ; l'entité juridique imbriquée est coupée par défaut
