FROM --platform=$BUILDPLATFORM python:3.12-slim

# System Dependencies
RUN apt-get update && apt-get install -y \
    curl wget gnupg ca-certificates docker.io \
    && rm -rf /var/lib/apt/lists/*

# Poetry/Pipx (global)
RUN pip install --no-cache-dir poetry pipx

# KEIN WORKDIR / COPY / RUN poetry – das nach Mount!

EXPOSE 8000 8001 3000
CMD ["/bin/sh"]  # Interaktiv für DevContainers
