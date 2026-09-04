# syntax=docker/dockerfile:1

# ---------- сборка зависимостей ----------
FROM python:3.12-slim AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PATH="/opt/venv/bin:$PATH"

RUN python -m venv /opt/venv

COPY requirements.txt /tmp/requirements.txt
RUN pip install --upgrade pip && pip install -r /tmp/requirements.txt


# ---------- рабочий образ ----------
FROM python:3.12-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/opt/venv/bin:$PATH" \
    MPLBACKEND=Agg \
    MPLCONFIGDIR=/home/app/.matplotlib \
    HOST=0.0.0.0 \
    PORT=8080 \
    DATABASE_URL=sqlite+aiosqlite:///./data/expenses.db

RUN useradd --create-home --uid 1000 app

COPY --from=builder /opt/venv /opt/venv

WORKDIR /app
COPY --chown=app:app run.py requirements.txt ./
COPY --chown=app:app app ./app
COPY --chown=app:app scripts ./scripts
COPY --chown=app:app tests ./tests

# data — база SQLite и кэш диаграмм; монтируется томом
RUN mkdir -p /app/data /home/app/.matplotlib \
    && chown -R app:app /app/data /home/app/.matplotlib

USER app

# Прогреваем кэш шрифтов matplotlib, иначе первая диаграмма строится секунд десять
RUN python -c "import matplotlib; matplotlib.use('Agg'); from matplotlib import font_manager; font_manager.fontManager"

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import sys, urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=3).status == 200 else 1)"

CMD ["python", "run.py"]
