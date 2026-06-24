FROM python:3.10-slim

WORKDIR /app

# Install essential system utilities
RUN apt-get update && apt-get install -y \
    build-essential \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy dependencies first to speed up builds
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy all repository files into the container image
COPY . .

# Inform Docker that the application listens on port 8080
EXPOSE 8080

# Run Streamlit, routing the port to Cloud Run's dynamic environment variable
CMD ["sh", "-c", "streamlit run app.py --server.port=${PORT:-8080} --server.address=0.0.0.0"]