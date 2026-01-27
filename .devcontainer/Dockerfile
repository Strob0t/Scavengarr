FROM mcr.microsoft.com/devcontainers/python:1-3.12-bookworm

# Yarn-Repo entfernen, damit apt-get update nicht wegen EXPKEYSIG scheitert
RUN rm -f /etc/apt/sources.list.d/yarn.list || true \
    && if [ -d /etc/apt/sources.list.d ]; then \
    for f in /etc/apt/sources.list.d/*.list; do \
    [ -f "$f" ] || continue; \
    grep -q "dl.yarnpkg.com/debian" "$f" && sed -i 's|^[[:space:]]*deb |# deb |' "$f" || true; \
    done; \
    fi
