# Use a lightweight Python image
FROM python:3.10-slim

# Set the working directory
WORKDIR /app

# Copy the clean requirements and install them
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of your app's code
COPY . .

# Expose the port Google Cloud Run expects
EXPOSE 8080

# The exact command to boot Streamlit on the cloud
CMD ["streamlit", "run", "app.py", "--server.port=8080", "--server.address=0.0.0.0"]