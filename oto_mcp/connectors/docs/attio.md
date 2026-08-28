## prerequisite — ta clé api attio

attio expose une clé api par workspace. va dans les [réglages développeur de ton workspace attio](https://app.attio.com), section **api**, et crée une clé (access token).
- colle-la dans oto sur ton compte (`/account`), connecteur **attio**
- pas de clé plateforme partagée : chacun pose la sienne
- pense à cocher les droits records + notes + tasks + lists selon ce que tu veux faire
- note : le connecteur mcp attio officiel est souvent préféré ; oto garde le code pour les implems custom

## usage — ce que tu peux faire

pilote ton crm attio (companies, people, deals) + notes, tasks, lists et comments depuis claude.
- « cherche l'entreprise acme » → `attio_record(op="search", object="companies")`, puis `attio_record(op="get", object="companies")` pour le détail
- « crée un contact jean dupont chez acme » → `attio_record(op="create", object="people")`
- « ajoute une note sur ce deal » → `attio_note(op="create")` (titre + markdown, attaché au record)
- « liste mes tâches en cours » → `attio_task(op="list")`, et `attio_task(op="create")` pour en ajouter une
