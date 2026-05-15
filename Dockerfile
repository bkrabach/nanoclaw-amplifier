# syntax=docker/dockerfile:1.7
FROM python:3.12-slim

# System deps
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    apt-get update && apt-get install -y --no-install-recommends \
        git curl ca-certificates tini cargo \
    && rm -rf /var/lib/apt/lists/*

# uv for fast installs
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

# Copy source (in CI these come from git; locally we copy from build context)
COPY . /app/nanoclaw-amplifier/

# Install amplifier-core and amplifier-foundation
# In production CI these are pinned git refs; for local build use local paths
ARG AMPLIFIER_CORE_SRC="git+https://github.com/microsoft/amplifier-core@main"
ARG AMPLIFIER_FOUNDATION_SRC="git+https://github.com/microsoft/amplifier-foundation@main"

RUN uv pip install --system --no-cache \
    "${AMPLIFIER_CORE_SRC}" \
    "${AMPLIFIER_FOUNDATION_SRC}"

# Install our tool modules and main package
RUN uv pip install --system --no-cache \
    /app/nanoclaw-amplifier/modules/tool-nanoclaw-messaging \
    /app/nanoclaw-amplifier/modules/tool-nanoclaw-scheduling \
    /app/nanoclaw-amplifier

# Install all Amplifier provider modules (pre-baked for performance)
RUN uv pip install --system --no-cache \
    "git+https://github.com/microsoft/amplifier-module-provider-anthropic@main" \
    "git+https://github.com/microsoft/amplifier-module-provider-openai@main" \
    "git+https://github.com/microsoft/amplifier-module-provider-gemini@main" \
    "git+https://github.com/microsoft/amplifier-module-provider-ollama@main" \
    "git+https://github.com/microsoft/amplifier-module-provider-chat-completions@main" \
    "git+https://github.com/microsoft/amplifier-module-provider-mock@main"

# Install orchestrator and context modules
RUN uv pip install --system --no-cache \
    "git+https://github.com/microsoft/amplifier-module-loop-streaming@main" \
    "git+https://github.com/microsoft/amplifier-module-context-simple@main"

# Workspace structure
RUN mkdir -p /workspace/agent/.amplifier /workspace/global /workspace/outbox && \
    chmod -R 777 /workspace

# Placeholder API keys so SDKs don't error before OneCLI proxy rewrites them
ENV ANTHROPIC_API_KEY=placeholder
ENV OPENAI_API_KEY=placeholder
ENV GEMINI_API_KEY=placeholder

ENTRYPOINT ["/usr/bin/tini", "--", "python", "-m", "nanoclaw_amplifier.runner"]
