# Azure OpenAI Discord Bot

Dockerized Discord bot application using Azure OpenAI to provide a ChatGPT-like experience with scoped memory and image generation.

## Features
- Mention-based chat in approved guild channels
- Direct message chat support
- Isolated memory per channel, thread, and DM scope
- Optional long-term memory extraction using simple heuristics
- Admin memory inspection, clearing, and toggling
- Image generation with metadata persistence
- PostgreSQL persistence with `pgvector`
- Health endpoints for container readiness and liveness

## Project structure
- [`app/main.py`](app/main.py)
- [`app/discord_client.py`](app/discord_client.py)
- [`app/config.py`](app/config.py)
- [`app/services/chat_service.py`](app/services/chat_service.py)
- [`app/services/image_service.py`](app/services/image_service.py)
- [`app/services/memory_service.py`](app/services/memory_service.py)
- [`app/services/rate_limit_service.py`](app/services/rate_limit_service.py)
- [`app/repositories/memory_repository.py`](app/repositories/memory_repository.py)
- [`db/init/001_enable_pgvector.sql`](db/init/001_enable_pgvector.sql)
- [`db/init/002_schema.sql`](db/init/002_schema.sql)
- [`docker-compose.yml`](docker-compose.yml)
- [`Dockerfile`](Dockerfile)
- [`.env.example`](.env.example)

## Prerequisites
- Docker Engine with Compose support
- Discord bot application and token
- Azure OpenAI resource with deployed chat, embedding, and image models

## Configuration
1. Copy [`.env.example`](.env.example) to [`.env`](.env.example).
2. Fill in the Discord and Azure OpenAI credentials.
3. Set [`DISCORD_ADMIN_USER_IDS`](.env.example) to one or more comma-separated Discord user IDs.
4. Adjust [`BOT_PERSONA`](.env.example) and [`SYSTEM_PROMPT_BASE`](.env.example) as needed.

## Build and publish image with GitHub Actions
A GitHub Actions workflow at [`.github/workflows/docker.yml`](.github/workflows/docker.yml) builds this image automatically.

### Publish behavior
- Push to the default branch publishes `ghcr.io/ratenS/azureapi-discordbot:latest`
- Push a Git tag matching `v*` publishes the matching version tag, such as `ghcr.io/ratenS/azureapi-discordbot:v0.0.1`
- Non-release builds also receive a traceable SHA tag
- Pull requests build the image for validation but do not push to GHCR

### Repository settings required
- The repository default branch should be `main`
- GitHub Actions must be enabled for the repository
- Package permissions must allow workflow publishing to GitHub Container Registry
- The workflow uses the built-in `GITHUB_TOKEN`, so no personal access token is required for the publish job

### Release flow
1. Merge changes into `main` to publish `latest`.
2. Create and push a Git tag such as `v0.0.1` to publish a versioned image.
3. Deploy pinned version tags in production for repeatable rollouts.

Example Windows cmd.exe commands:
```bat
git tag v0.0.1
git push origin v0.0.1
```

## Deploy with Docker Compose
### 1. Authenticate on the deployment host
If the package is private, log into GHCR before starting Compose:
```bat
echo YOUR_GITHUB_TOKEN | docker login ghcr.io -u ratenS --password-stdin
```

### 2. Pull the pinned release image
```bat
docker compose pull
```

### 3. Start the stack
```bat
docker compose up -d
```

The application now bootstraps PostgreSQL extensions and schema from [`db/init/001_enable_pgvector.sql`](db/init/001_enable_pgvector.sql) and [`db/init/002_schema.sql`](db/init/002_schema.sql) during startup before running memory cleanup. The Docker-mounted init scripts remain useful for first-time database creation, but the app no longer depends on container first-run behavior for table creation.

### 4. View logs
```bat
docker compose logs -f azure-discord-bot
```

### 5. Stop the stack
```bat
docker compose down
```

Use [`ghcr.io/ratenS/azureapi-discordbot:v0.0.1`](docker-compose.yml) for repeatable deployments. Reserve `latest` for branch-based testing and validation.

## Run locally without Docker
### 1. Create a virtual environment
```bat
python -m venv .venv
.venv\Scripts\activate
```

### 2. Install dependencies
```bat
python -m pip install --upgrade pip
pip install -r requirements.txt
```

### 3. Start PostgreSQL separately
Use [`docker-compose.yml`](docker-compose.yml) or your own PostgreSQL instance with `pgvector` enabled.

### 4. Set environment variables
Ensure [`.env`](.env.example) is present and points [`DATABASE_URL`](.env.example) to the correct database host.

### 5. Run the app
```bat
python -m app.main
```

## Health checks
- Liveness: `http://localhost:8080/health/live`
- Readiness: `http://localhost:8080/health/ready`

## Discord usage
- In guild channels, mention the bot to chat.
- In DMs, send messages directly.
- Use slash commands such as:
  - `/image`
  - `/memory inspect`
  - `/memory clear`
  - `/memory enable`
  - `/memory disable`
  - `/bot enable-channel`
  - `/bot disable-channel`

## Notes and current v1 limitations
- Embedding persistence schema is prepared, but semantic vector search is currently scaffolded and not yet fully implemented.
- User profile memory toggle commands are stubbed for a later persistence migration.
- Approved channels and image enablement are controlled through stored scope settings; first-run bootstrap may require inserting initial enabled records.
- Media generation is limited to images in v1.

## Recommended next steps
- Add full embedding generation and vector similarity retrieval in [`app/services/memory_service.py`](app/services/memory_service.py)
- Persist profile memory preferences in [`user_profiles`](db/init/002_schema.sql)
- Add richer admin audit logging in [`admin_audit_log`](db/init/002_schema.sql)
- Add automated tests and migration tooling
