# Agent Router requires its supported Claude Code client for judge requests.
FROM node:22-bookworm-slim AS claude-runtime
RUN npm install -g @anthropic-ai/claude-code@2.1.217 @openai/codex@0.159.0-alpha.12.1 && claude --version && codex --version

FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /srv
RUN apt-get update && apt-get install -y --no-install-recommends libstdc++6 ca-certificates && rm -rf /var/lib/apt/lists/*

COPY --from=claude-runtime /usr/local/lib/node_modules/@anthropic-ai/claude-code/bin/claude.exe /usr/local/bin/claude
COPY --from=claude-runtime /usr/local/bin/node /usr/local/bin/node
COPY --from=claude-runtime /usr/local/lib/node_modules/@openai /usr/local/lib/node_modules/@openai
RUN ln -s /usr/local/lib/node_modules/@openai/codex/bin/codex.js /usr/local/bin/codex && claude --version && codex --version

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY app ./app
COPY supabase ./supabase

# Uploaded images are cached here; the source of truth is the Supabase bucket, so this can be wiped.
ENV IMAGE_JUDGE_UPLOADS=/tmp/uploads

# One process only: benchmark runs are background jobs held in memory.
# --proxy-headers lets the app see that the browser used https behind Railway's proxy (secure cookies).
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers --forwarded-allow-ips='*'"]
