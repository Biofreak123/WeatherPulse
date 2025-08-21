# Use official lightweight Python image
FROM python:3.11-slim

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Set working directory
WORKDIR /app

# Create venv
RUN python -m venv .venv

# Install dependencies
COPY Requirements.txt ./requirements.txt
RUN .venv/bin/pip install --upgrade pip && .venv/bin/pip install --no-cache-dir -r requirements.txt

# Copy app source
COPY . .

# Expose port
EXPOSE 8080

# Run with Gunicorn (entrypoint: app.py -> app)
CMD [".venv/bin/gunicorn", "-w", "4", "-b", "0.0.0.0:8080", "app:app"]
