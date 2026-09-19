FROM python:3.13-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY migrations ./migrations

# One worker, on purpose. The note sequence is serialised with an advisory lock
# and the write rate is a camp, not a website; more workers would buy nothing
# and cost the simplicity of knowing there is exactly one writer.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
