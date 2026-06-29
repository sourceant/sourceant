## Deployment

### Container Images

Official Docker images are available on GHCR and are rebuilt automatically on every merge to `main`:

```bash
docker pull ghcr.io/sourceant/sourceant:latest
```

Run the container with the required environment variables:

```bash
docker run -d \
  --name sourceant \
  -p 8000:8000 \
  -v /path/to/.env:/app/.env \
  -v /path/to/private-key.pem:/app/private-key.pem \
  ghcr.io/sourceant/sourceant:latest
```

### Docker Compose (Development)

For local development, Docker Compose bundles the API server, a Redis queue, and a PostgreSQL database:

```bash
docker compose up -d
```

### Building Locally

Build a custom image in your local checkout:

```bash
make prod-build
make prod-build IMAGE_TAG=v1.0.0
make prod-push
```

### Commands

| Command | Description |
|---|---|
| `sourceant db upgrade head` | Run database migrations |
| `sourceant db --help` | Database command help |
| `rq worker --url redis://redis:6379` | Start a background worker for Redis queue mode |

### Enterprise Image

The enterprise image includes additional plugins. To build:

1. Add a repository secret `PLUGIN_REPO_TOKEN` (a PAT with repo access to clone private plugin repositories).
2. Add a repository variable `ENTERPRISE_PLUGINS` with your plugin configuration.
3. Run the **Build Enterprise Image** GitHub Action workflow.

### Environment Overrides

Copy `docker-compose.override.yml.example` to `docker-compose.override.yml` to customise ports, volumes, or environment variables for your deployment.
