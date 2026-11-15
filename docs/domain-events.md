# Domain events

Core reports review failures and incomplete coverage as domain events. User-facing notification storage, presentation, recipients, preferences, read state, and email delivery belong to the gateway.

Set `DOMAIN_EVENTS_URL` to the gateway's internal event endpoint and `DOMAIN_EVENTS_SECRET` to the signing secret shared with the gateway's `CORE_EVENTS_SECRET`. Leave the URL unset for standalone Core deployments.

Events contain a version, unique ID, type, UTC occurrence time, workspace ID, and review/repository/provider/model references. Supported types are `review.failed`, `review.coverage_incomplete`, `provider.credits_exhausted`, and `provider.authentication_failed`. Events exclude raw provider errors, source content, and credentials.

The delivery worker posts JSON with `X-Sourceant-Timestamp` and `X-Sourceant-Signature`. The signature is the hexadecimal HMAC-SHA256 of the timestamp, a period, and the exact request body. The gateway accepts timestamps within five minutes and deduplicates event IDs.

The background job lane owns delivery retries. A failed review does not rerun to deliver its event. A persisted review failure and its event enqueue share a database transaction. Exhausted delivery jobs remain available for operator inspection and replay through the existing job administration tools.

`CodeReview.coverage_complete` describes whether all planned passes were available. The plugin also considers unread repositories and artifact-only work when emitting incomplete-coverage events. Cancelled and continuing runs do not emit terminal failure events.
