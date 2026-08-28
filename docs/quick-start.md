## Quick Start

SourceAnt holds an indexed graph of your codebase and the decisions behind it. Review and triage are built on that graph.

To index repositories on your own machine with nothing configured, start at [Local index](local-index.md). This walkthrough gets a full instance running and answering GitHub events.

### Prerequisites

- Docker and Docker Compose
- An LLM API key (Gemini, Anthropic, OpenAI, DeepSeek, or any other provider LiteLLM supports)
- A GitHub repository to connect

### Install

```bash
git clone https://github.com/sourceant/sourceant.git
cd sourceant
cp .env.example .env
```

Create `.env` before starting anything: Compose loads it into the app container and will not start without it.

Set at least your model and its API key:

```env
LLM_MODEL=gemini/gemini-2.5-flash
GEMINI_API_KEY=your_gemini_api_key
```

See [Configuration](configuration.md) for the rest.

### Start

```bash
docker compose up -d
docker compose exec -T app sourceant db upgrade head
```

Reviews run on a Redis queue by default, so a worker has to be running for anything to be processed:

```bash
docker compose exec -T app rq worker --url redis://redis:6379
```

`make worker` runs the same command.

### Verify

```bash
curl http://localhost:8000/
```

```json
{"message":"The 🐜 SourceAnt 🐜  API is live!"}
```

The interactive API reference is at `http://localhost:8000/docs`.

### Connect a repository

GitHub delivers events to `POST /api/prs/github-webhook`, so your instance needs an address GitHub can reach. For a local instance, expose port 8000 through a tunnel and use that address when you create a [GitHub App](github-app.md).

### Next steps

- [Code Review](reviews.md): what SourceAnt posts on a pull request, and how to shape it.
- [Lens](lens.md): the risk-ranked way to read a change, in SourceAnt Cloud.
- [Triage](triage.md): work the open-issue queue.
- [Systems](systems.md): map how repositories depend on each other.
- [Knowledge and Context](context.md): give agents the decisions and rules behind the code.
- [Deployment](deployment.md): run it somewhere permanent.
