## prerequisite — a google service account, viewer of the GA4 property

access goes through a Google **service account**, not your Google account: it reads GA4 without anyone connecting their account, and is cut off by removing its access in GA4. an administrator sets it up once for the whole org (org instance); every agent in the org then reads the same properties, with nothing to install.

- in a Google Cloud project, **enable** "Google Analytics Data API" and "Google Analytics Admin API" ([Google docs](https://developers.google.com/analytics/devguides/reporting/data/v1/quickstart-client-libraries))
- create a service account, then a **JSON key** ([Google docs](https://cloud.google.com/iam/docs/keys-create-delete)): the whole file is what you paste into `service_account_json`, exactly as downloaded
- in GA4, add the **service account's email** (`…@….iam.gserviceaccount.com`) as a **Viewer** of each property to read ([Google docs](https://support.google.com/analytics/answer/9305788))
- "test the connection" calls the list of visible properties: zero properties is a failure, not a green — it is almost always the missing GA4 step
- no OAuth: a Google account's consent to the `analytics.readonly` scope is blocked by Google for this application

## usage — read a GA4 property's audience and events

read-only, nothing is ever written to GA4.

- "which properties can we read?" → `ga4_properties()`; `include_streams=true` adds the streams (website, measurement ID `G-…`)
- "events over the last 30 days" → `ga4_report(property="123456789", metrics=["eventCount"], dimensions=["eventName"], order_by=["-eventCount"])` — without dates, the window is the last 30 full days (`30daysAgo` → `yesterday`)
- "sessions by channel in September" → `ga4_report(…, metrics=["sessions"], dimensions=["sessionDefaultChannelGroup"], start_date="2026-09-01", end_date="2026-09-30")`
- simple filter: `dimension_filter={"country": ["France", "Belgique"]}` (text = equality, list = any of the values, combined with AND); other operators go through a GA4 FilterExpression
- "who is on the site right now?" → `ga4_realtime(property=…, dimensions=["country"])` — no rows = nobody active, not an error
- "which dimensions and metrics exist?" → `ga4_metadata(property=…)`, or `search="revenue"` for the detail of one entry; to consult as soon as a report is refused for an invalid name
- "which conversions are tracked?" → `ga4_key_events(property=…)`

## note — read a GA4 figure without getting it wrong

- names are the **API names** (`activeUsers`, `sessionDefaultChannelGroup`), not the labels of the GA4 interface; an invalid name is refused with Google's message naming it
- `metadata` in a report's response carries GA4's warnings: `samplingMetadatas` (sampled figures), `subjectToThresholding` (small counts hidden for privacy), `dataLossFromOtherRow` (rare values grouped into "(other)") — to mention alongside the figures
- `row_count` is the total of matching rows; `next_offset` appears when more remain
- a "has no access" refusal names the service account's email: that is the one to add as a Viewer of the property
