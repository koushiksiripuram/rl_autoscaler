FROM python:3.9-slim

WORKDIR /app

# Install kubectl (IMPORTANT)
RUN apt-get update && apt-get install -y curl && \
    curl -LO "https://dl.k8s.io/release/$(curl -L -s https://dl.k8s.io/release/stable.txt)/bin/linux/amd64/kubectl" && \
    install -o root -g root -m 0755 kubectl /usr/local/bin/kubectl && \
    rm kubectl

# Copy project
COPY . .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt
COPY start.sh /app/
# Run autoscaler
RUN chmod +x /app/start.sh
EXPOSE 8501
CMD ["/app/start.sh"]
