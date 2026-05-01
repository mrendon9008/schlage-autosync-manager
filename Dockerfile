FROM python:3.12-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy app files
COPY backend/ ./backend/
COPY static/ ./static/
COPY start_server.py .
COPY SCHEMA.md . 2>/dev/null || true

# Create data directory for SQLite
RUN mkdir -p /app/data

# Expose port
EXPOSE 8000

# Run the app
CMD ["python", "start_server.py"]
