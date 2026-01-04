# SSH Remote Connection

Universal skill for connecting to remote servers via SSH.

## Usage

```bash
# From project root:
.claude/skills/ssh-remote-connection/scripts/connect.sh
```

Or run commands directly:
```bash
.claude/skills/ssh-remote-connection/scripts/connect.sh "docker compose logs backend --tail 50"
```

## Setup

1. Copy config template:
   ```bash
   cp .claude/skills/ssh-remote-connection/config/.env.example \
      .claude/skills/ssh-remote-connection/config/.env
   ```

2. Fill in `.env` with actual values

3. Make script executable:
   ```bash
   chmod +x .claude/skills/ssh-remote-connection/scripts/connect.sh
   ```

## Important Notes

- **Git operations**: Do NOT run `git pull` on the server. User will handle git sync manually.
- **Code location**: Code is in a private repo, changes must be pushed first then pulled by user.
- **Docker**: Use `docker compose` (not `docker-compose`) on the server.

## Common Commands

```bash
# View logs
docker compose logs backend --tail 100
docker compose logs mcp_ui_control --tail 100
docker compose logs frontend --tail 100

# Restart services
docker compose restart backend
docker compose restart mcp_ui_control

# Rebuild and restart
docker compose build backend && docker compose up -d backend

# Check Redis
docker compose exec redis redis-cli KEYS "session:*"
```
