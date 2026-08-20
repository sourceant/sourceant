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
  --entrypoint rq \
  ghcr.io/sourceant/sourceant:latest worker --url redis://your-redis:6379
```

### Docker Compose

Compose brings up the API, PostgreSQL, and Redis together, and is the development path:

```bash
cp .env.example .env
docker compose up -d
docker compose exec -T app sourceant db upgrade head
```

Copy `docker-compose.override.yml.example` to `docker-compose.override.yml` to mount local plugin directories into the app container.

### Building your own image

```bash
make prod-build
make prod-build IMAGE_TAG=v1.0.0
make prod-push
```

`IMAGE_NAME` and `IMAGE_TAG` override the target, which defaults to `ghcr.io/sourceant/sourceant:latest`.

### Enterprise image

The enterprise image is the same application with private plugins built in. It is built and published as `ghcr.io/sourceant/enterprise` from the `sourceant/enterprise` repository, which pins the core image and each plugin revision, and rebuilds on every merge to its main branch.

### Commands

| Command | What it does |
|---|---|
| `sourceant db upgrade head` | Apply migrations |
| `sourceant db --help` | Every database subcommand |
| `rq worker --url redis://redis:6379` | Run a queue worker |

### Checklist for a production instance

- `DATABASE_URL` points at PostgreSQL, and migrations have been applied.
- `GITHUB_SECRET` is set, and matches the secret on the webhook.
- `JWT_SECRET` is set if anything uses the API.
- `QUEUE_MODE=redis` with at least one worker running.
- `LOG_DRIVER=console`, so your platform collects the logs.
- `APP_ENV=production` and `DEBUG_MODE=false`.
