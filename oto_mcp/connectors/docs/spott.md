## prerequisite — your spott api key

you need a **spott api key**.
- in spott, go to **settings → api keys** and generate a key
- paste it into your [connector keys](https://manage.oto.cx/) (or let your org share its own)
- vendor docs: [api-docs.spott.io](https://api-docs.spott.io)

## usage — what you can do

drive spott, the ats **and** crm of a recruitment firm: the candidate on one side, the client company on the other. a position = a **job** (`vacancy` in the api urls), a candidate on a position = an **application** that moves forward from **stage** to **stage**.
- "do we already know john smith?" → `spott_people` (searches candidates **and** client contacts, fuzzy) — do this before creating anything
- "list the candidates" → `spott_candidate(op="list")`, detail → `spott_candidate` ; by criteria → `spott_candidate(op="search")`
- "create a candidate" → `spott_candidate(op="create")` (`firstName`/`lastName` required), correction → `spott_candidate(op="update")`
- "which positions are open?" → `spott_job(op="search")` with the `vacancy.stage.isOpen` filter ; the raw list → `spott_job(op="list")`, detail → `spott_job`
- "where do the applications for position X stand?" → `spott_application(op="list")` (`job_id`, or `candidate_id` for the reverse)
- "apply this candidate" → `spott_stages` (get the stage id) then `spott_application(op="create")` ; "move them to interview" → `spott_application(op="move")`
- "log yesterday's call" → `spott_note(op="create")` (`links` to the candidate, `source` phone/inPerson…), read back → `spott_note(op="list")`
- crm side: `spott_client(op="list")` (list, or search if you pass `filters`), `spott_client`, `spott_client(op="contacts")`, and `spott_placements` for closed placements and their fees
