FROM python:3.12-slim
WORKDIR /app
RUN pip install --no-cache-dir uv
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-install-project
COPY src/ src/
COPY data/ data/
RUN uv sync --locked --no-dev && mkdir -p /app/data /app/logs
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD /app/.venv/bin/python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health', timeout=3)" || exit 1
CMD ["/app/.venv/bin/uvicorn", "psych_support_bot.app:app", "--host", "0.0.0.0", "--port", "8000"]
