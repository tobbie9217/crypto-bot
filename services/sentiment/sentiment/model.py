import structlog
import torch
from transformers import AutoModelForSequenceClassification, AutoTokenizer

log = structlog.get_logger()

# CryptoBERT card lists labels in this order: Bearish, Neutral, Bullish.
LABEL_ORDER = ["Bearish", "Neutral", "Bullish"]


class CryptoBERT:
    def __init__(self, model_name: str, max_tokens: int = 256) -> None:
        self.tokenizer = AutoTokenizer.from_pretrained(model_name)
        self.model = AutoModelForSequenceClassification.from_pretrained(model_name)
        self.model.eval()
        self.max_tokens = max_tokens
        log.info("model_loaded", name=model_name)

    @torch.no_grad()
    def score_batch(self, texts: list[str]) -> list[tuple[float, str, float]]:
        """Score a list of texts. Returns [(score, label, confidence), ...].

        score: bullish_prob - bearish_prob, in [-1, 1].
        confidence: softmax probability of the picked label, in [0, 1].
        """
        if not texts:
            return []
        inputs = self.tokenizer(
            texts, padding=True, truncation=True,
            max_length=self.max_tokens, return_tensors="pt",
        )
        logits = self.model(**inputs).logits
        probs = torch.softmax(logits, dim=-1).cpu().numpy()

        out: list[tuple[float, str, float]] = []
        for p in probs:
            idx = int(p.argmax())
            score = float(p[2] - p[0])
            confidence = float(p[idx])
            out.append((score, LABEL_ORDER[idx], confidence))
        return out
