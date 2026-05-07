FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
        gcc libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# CPU-only torch first (saves ~800MB vs the default GPU wheels)
RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Pre-bake CryptoBERT into the image so first start doesn't stall
# downloading ~440MB of weights.
RUN python -c "from transformers import AutoTokenizer, AutoModelForSequenceClassification; \
    AutoTokenizer.from_pretrained('ElKulako/cryptobert'); \
    AutoModelForSequenceClassification.from_pretrained('ElKulako/cryptobert')"

COPY . .

CMD ["python", "-m", "sentiment.main"]
