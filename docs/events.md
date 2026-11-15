# Events

Core reports review failures and incomplete coverage as events. User-facing notification storage, presentation, recipients, preferences, read state, and email delivery belong to the gateway.

Set `EVENTS_URL` to the gateway's internal event endpoint and `EVENTS_SECRET` to the signing secret shared with the gateway's `CORE_EVENTS_SECRET`. Leave the URL unset for standalone Core deployments.

Events use the existing Core subscriber registry. Subscribe with `event_hooks.subscribe_to_events` and emit with `src.core.events.emit(type="worker.started", data={"worker": "background"})`. Local listeners do not require a gateway or a workspace. Synchronous dispatch propagates listener failures and rejects asynchronous listeners before invoking any subscriber. The existing asynchronous `broadcast_event` path remains available for plugin broadcasts.

A forwarding listener subscribes to selected events and queues their delivery. Transactional listeners opt in with `transactional=True` and receive `session` as a keyword argument; they must save their work using that transaction. Ordinary listeners receive the event type and envelope. A rollback cannot undo a listener’s external side effects, so transactional listeners must defer those effects through queued work.

Forwarded review events contain a version, unique ID, type, UTC occurrence time, workspace ID, and review/repository/provider/model references. The forwarding listener handles `review.failed`, `review.coverage_incomplete`, `provider.credits_exhausted`, and `provider.authentication_failed`. These review events exclude raw provider errors, source content, and credentials.

The delivery worker posts JSON with `X-Sourceant-Timestamp` and `X-Sourceant-Signature`. The signature is the hexadecimal HMAC-SHA256 of the timestamp, a period, and the exact request body. The gateway accepts timestamps within five minutes and dispatches a Laravel `CoreEventReceived` event. A notification listener handles review and provider failures. Other listeners can consume events without creating notifications. Each consumer must deduplicate event IDs because delivery may repeat; the notification listener already does so.

The background job lane owns delivery retries. A failed review does not rerun to deliver its event. A persisted review failure and its event enqueue share a database transaction. Exhausted delivery jobs remain available for operator inspection and replay through the existing job administration tools.

`CodeReview.coverage_complete` describes whether all planned passes were available. The plugin also considers unread repositories and artifact-only work when emitting incomplete-coverage events. Cancelled and continuing runs do not emit terminal failure events.
