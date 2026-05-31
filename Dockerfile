# Monorepo deploy: bot + API live under server/
FROM python:3.13-slim

WORKDIR /app

COPY server/requirements.txt server/requirements.txt
RUN pip install --no-cache-dir -r server/requirements.txt

COPY server/ server/

CMD ["python", "server/run.py"]
