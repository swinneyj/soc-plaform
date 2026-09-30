# Multi-stage build: builder stage to install dependencies, runtime stage minimal
FROM python:3.14-slim as builder

WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements and install from actual file
COPY requirements.txt .
RUN pip install --no-cache-dir --user -r requirements.txt

# Runtime stage
FROM python:3.14-slim

WORKDIR /app

# Copy only necessary Python packages from builder
COPY --from=builder /root/.local /root/.local

# Set environment to use local pip installs
ENV PATH=/root/.local/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    SOC_PLATFORM_ROOT=/app

# Install runtime dependencies (curl for healthcheck + ca-certificates)
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Copy entire platform
COPY . .

# Create data directories with proper permissions
RUN mkdir -p Data/Archive Data/Reports Data/Logs Data/Active_Workspace Playbooks web && \
    chmod -R 755 Data Playbooks Tools web

# Expose API port
EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# Default to API service; can override with 'docker run ... python commander.py' for CLI
CMD ["python", "-m", "uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]
