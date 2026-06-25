## Deployment

### Docker Compose

The recommended deployment method uses Docker Compose. The compose file includes the API server, a Redis queue, and a PostgreSQL database.

```bash
docker compose up -d
```

### Docker Images

Official Docker images are built automatically on merge to `main` and pushed to GHCR:

- **Base image:** `ghcr.io/sourceant/sourceant:latest`
- **Enterprise image:** `ghcr.io/sourceant/sourceant-enterprise:latest`

Build locally:

```bash
make prod-build
make prod-build IMAGE_TAG=v1.0.0
make prod-push
```

### Commands Reference

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
