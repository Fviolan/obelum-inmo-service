FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1

WORKDIR /app

# certificados al día: muchas webs de leads tienen cadenas SSL incompletas
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install -r requirements.txt

# app.py importa los tres validadores: si no se copian, el contenedor arranca
# y muere con ModuleNotFoundError
COPY app.py cifras.py credibilidad.py ejemplos.py ./
COPY skill/ ./skill/
COPY prompts/ ./prompts/

EXPOSE 8000
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000", "--timeout-keep-alive", "120"]
