FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Install dependencies first (better layer caching)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY main.py .

# Non-root user for security (production best practice)
RUN useradd -m -u 1000 appuser && chown -R appuser:appuser /app
USER appuser

# Expose port
EXPOSE 8000

# Start the service
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]