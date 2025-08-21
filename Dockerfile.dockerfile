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
COPY Requirements.txt ./
RUN .venv/bin/pip install --upgrade pip && .venv/bin/pip install --no-cache-dir -r Requirements.txt

# Copy app source
COPY . .

# Expose port
EXPOSE 8080

# Run Flask (through the venv)
CMD [".venv/bin/flask", "run", "--host=0.0.0.0", "--port=8080"]
