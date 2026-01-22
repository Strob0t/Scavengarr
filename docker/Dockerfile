FROM python:3.12-slim
COPY ./ /app
WORKDIR /app
RUN which poetry || pipx install poetry; export PATH=\"$HOME/.local/bin:$PATH\"; poetry config virtualenvs.in-project true; [ -f pyproject.toml ] && poetry install
CMD ["uvicorn", "src.scavengarrapplication.main:app", "--host", "0.0.0.0", "--port", "7979"]
