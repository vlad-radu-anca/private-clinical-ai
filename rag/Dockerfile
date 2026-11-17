# The RAG service: indexer and API in one image. The same image runs next to
# HAPI on a laptop and on EKS in AWS; only environment variables differ.
FROM python:3.13-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
 && useradd --uid 10001 --create-home rag
COPY clinical_rag ./clinical_rag
USER 10001
EXPOSE 8000
CMD ["uvicorn", "clinical_rag.api:app", "--host", "0.0.0.0", "--port", "8000"]
