## prerequisite — your greenhouse (harvest) api key

you need a greenhouse **harvest api** key.
- in greenhouse, go to **configure → dev center → api credentials** and create a key of type *harvest*
- give it the candidates/jobs/applications/users permissions
- paste it into your [connector keys](https://manage.oto.cx/) (or let your org share its own)
- vendor docs: [greenhouse.io](https://www.greenhouse.io)
- ⚠️ writes (creating a candidate, adding a note) require an `on_behalf_of` = the id of a greenhouse user, retrieved via `greenhouse_users`

## usage — what you can do

drive your greenhouse ats from the conversation: candidates, jobs, applications, notes.
- "list the candidates on job 123" → `greenhouse_candidate(op="list")` (filters `job_id`, `email`, `created_after`)
- "show me candidate 456 and their applications" → `greenhouse_candidate`
- "add a note on candidate 456" → `greenhouse_candidate(op="add_note")` (needs an author `user_id`, see `greenhouse_users`)
- "which jobs are open?" → `greenhouse_job(op="list")` (`status` open/closed/draft)
