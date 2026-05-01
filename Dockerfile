FROM python:3.12-slim

# Install git for fetching PySchlage from GitHub
RUN apt-get update && apt-get install -y --no-install-recommends git && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/ ./backend/
COPY static/ ./static/
COPY start_server.py .

RUN mkdir -p /app/data

EXPOSE 8000

CMD ["python", "start_server.py"]
