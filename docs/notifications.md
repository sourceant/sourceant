# Workspace notifications

Notifications record credit exhaustion, rejected model credentials and interrupted reviews.
They appear under the dashboard bell and are emailed to the workspace owner.
The gateway scheduler queues pending emails every minute.

An alert contains a title, a safe explanation and a dashboard action.
Raw provider responses, request IDs and credentials stay out of notifications.
Credit exhaustion in a downstream memory service is recognized from its error too.

Repeated reports of the same problem in the same workspace are grouped for one UTC hour.
Reading an alert affects only the signed-in user's read status. It does not cancel email delivery.
Notifications report failed requests; they do not poll provider balances or predict when credits will run out.

Core callers can publish another scenario with `src.core.notifications.publish`.
Provide the workspace, a stable notification type, title and safe message.
The source distinguishes separate problems. Actions must be dashboard paths.

## API

- `GET /api/notifications`: list the signed workspace's notifications and unread count.
  Supports `limit`, `offset` and `unread_only`.
- `POST /api/notifications/{id}/read`: mark one notification read for the signed-in user.

The gateway proxies these endpoints after checking workspace membership.
An ID from another workspace returns 404.

## Email delivery

The email outbox is committed with the notification. A gateway token with the
`sourceant:notifications` audience can list pending email IDs, claim a delivery,
acknowledge it or release a failed attempt. User and editor tokens cannot use these endpoints.
Each claim expires after 15 minutes, so a crashed worker does not lose the email.
Reading or successfully emailing an alert does not resolve the underlying failure.

The gateway runs `notifications:deliver` every minute and queues unique delivery jobs.
The job resolves the current active workspace owner before sending. Removed workspaces
are skipped. Failed sends release their claims for retry. Successful sends are acknowledged.
Delivery is at least once: a crash after sending but before acknowledgement can send a duplicate.

Deploy the Core migration before the gateway and run both its queue worker and scheduler.
The enterprise stack includes a `webservice-scheduler` service for this.
