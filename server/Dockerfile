FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt

# Optional runtime tracing dependencies are installed for the server image;
# tracing remains disabled unless MEM0_RUNTIME_TRACING=true.
COPY requirements-tracing.txt /tmp/requirements-tracing.txt
RUN pip install --no-cache-dir -r /tmp/requirements-tracing.txt

COPY . .

EXPOSE 8000

ENV PYTHONUNBUFFERED=1

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]
