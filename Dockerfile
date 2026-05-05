# Use an official Python runtime as a parent image
FROM python:3.9-slim

# Set the working directory in the container
WORKDIR /app

# Copy the requirements file into the container
COPY requirements.txt .

# Install any needed packages specified in requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the application code into the container
COPY . .

# Create a writable directory for the database if needed, 
# although sqlite will create it in the current WORKDIR.
# Hugging Face Spaces runs with user ID 1000, so we ensure permissions.
RUN chmod -R 777 /app

# Hugging Face Spaces listens on port 7860 by default
ENV PORT=7860
EXPOSE 7860

# Start the application using gunicorn with eventlet for SocketIO
CMD ["sh", "-c", "gunicorn --worker-class eventlet -w 1 --bind 0.0.0.0:$PORT app:app"]
