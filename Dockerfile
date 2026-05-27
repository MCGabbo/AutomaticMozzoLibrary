FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY book.py bot.py users.py ./

RUN useradd -r -u 1000 -m -d /home/app app && chown -R app:app /app
USER app

CMD ["python", "bot.py"]
