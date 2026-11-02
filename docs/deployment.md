## Deployment

### Container images

Images are published to GHCR and rebuilt on every merge to `main`:

```bash
docker pull ghcr.io/sourceant/sourceant:latest
```

Every build is also tagged with its commit sha, which is what you want to pin to for a reproducible deployment.

The container runs migrations and then serves the API on port 8000. It needs a PostgreSQL database and, in the default queue mode, a Redis instance, both reachable from inside the container:

```bash
docker run -d \
  --name sourceant \
  -p 8000:8000 \
  -v /path/to/.env:/app/.env \
  -v /path/to/private-key.pem:/app/private-key.pem \
  ghcr.io/sourceant/sourceant:latest
```

Run at least one worker against the same configuration, or queued reviews are never processed:

```bash
docker run -d --name sourceant-worker \
  -v /path/to/.env:/app/.env \
  --entrypoint sourceant \
  ghcr.io/sourceant/sourceant:latest work --lane interactive
```

### Docker Compose

Compose brings up the API, PostgreSQL, and Redis together, and is the development path:

```bash
cp .env.example .env
docker compose up -d
docker compose exec -T app sourceant db upgrade head
```

Copy `docker-compose.override.yml.example` to `docker-compose.override.yml` to mount local plugin directories into the app container.

### Event memory

Compose includes the AGPL memory engine and its own PostgreSQL volume, under the
`memory` profile. Knowledge
stays in SourceAnt's knowledge store; memory records events from GitHub,
knowledge changes, and the MCP `remember` tool.

Set `MEMORY_DB_PASSWORD` in `.env`, start the services, then create an engine
workspace and a service key. Register the GitHub installation IDs that send
webhooks to this deployment:

If the `ghcr.io/trailpad/memory` image cannot be pulled, build it from a checkout
of the engine with `docker build -t ghcr.io/trailpad/memory:latest .`.

```bash
docker compose --profile memory up -d memory
docker compose exec memory /memoryd workspace -id sourceant
docker compose exec memory /memoryd service -workspace sourceant -id sourceant -actions read,write,sync -installations sourceant/local,github/your-installation-id
```

Put the returned key in `MEMORY_ENGINE_KEY` and set
`MEMORY_ENGINE_URL=http://memory:8080` in `.env`, then recreate the app. The
`sourceant/local` installation covers local knowledge and MCP events. For hosted
workspace scopes, register `sourceant/your-workspace-id` too. Do not publish the
engine port. With neither engine setting configured, events are held in process
and disappear when the app restarts.

Replay stored GitHub deliveries with `sourceant memory-replay`, optionally
followed by repository names. Repeating a replay preserves existing events.

### Building your own image

```bash
make prod-build
make prod-build IMAGE_TAG=v1.0.0
make prod-push
```

`IMAGE_NAME` and `IMAGE_TAG` override the target, which defaults to `ghcr.io/sourceant/sourceant:latest`.

### Commands

| Command | What it does |
|---|---|
| `sourceant db upgrade head` | Apply migrations |
| `sourceant db --help` | Every database subcommand |
| `sourceant work --lane interactive` | Run a queue worker |
| `rq worker --url redis://redis:6379` | Run a queue worker, with `QUEUE_MODE=redis` |

### Checklist for a production instance

- `DATABASE_URL` points at PostgreSQL, and migrations have been applied.
- `GITHUB_SECRET` is set, and matches the secret on the webhook.
- `JWT_SECRET` is set, and matches whatever else signs the tokens this instance verifies.
- At least one `sourceant work` process per lane, or `QUEUE_MODE=redis` with an `rq` worker.
- `LOG_DRIVER=console`, so your platform collects the logs.
- `APP_ENV=production` and `DEBUG_MODE=false`.
