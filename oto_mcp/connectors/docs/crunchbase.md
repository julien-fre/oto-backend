## prerequisite — capture your crunchbase session (cookie)

Crunchbase has no public API key: oto replays your **logged-in session**. capture the session cookies (+ user-agent) of your [Crunchbase](https://www.crunchbase.com) account from the **account** page of the oto dashboard.
- without a configured session, the `crunchbase_*` tools return a message pointing to the account page

## usage — crunchbase companies, funding and people

fetch company data, their funding rounds and people profiles.
- "Crunchbase record for `anthropic` (headcount, location, founders)"
- "list this company's funding rounds (date, type, amount, investors)"
- "search companies on `vector database`"
- "Crunchbase profile of this person"
