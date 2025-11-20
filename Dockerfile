FROM python:3.12-slim

# Install system deps (for Pillow fonts etc.)
RUN apt-get update && apt-get install -y \
    libjpeg-dev zlib1g-dev libpng-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy app and assets
COPY . .

ENV PORT=8080

CMD ["python", "app.py"]
