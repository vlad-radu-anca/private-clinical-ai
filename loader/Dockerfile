FROM python:3.13-slim
RUN pip install --no-cache-dir "boto3==1.43.108" \
 && useradd --uid 10001 --create-home loader
COPY load_fhir.py /app/load_fhir.py
USER 10001
WORKDIR /home/loader
ENTRYPOINT ["python", "/app/load_fhir.py"]
