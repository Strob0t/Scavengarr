FROM python:3.12-slim AS builder
RUN pip install poetry
COPY pyproject.toml poetry.lock ./
RUN poetry export -f requirements.txt --output requirements.txt --without-hashes

FROM python:3.12-slim
COPY --from=builder requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
RUN playwright install chromium --with-deps
COPY src/ /app/src/
CMD ["python", "-m", "scavengarr.interfaces.cli"]
