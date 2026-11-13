#!/usr/bin/env bash
# Generate synthetic patients with Synthea as FHIR R4 transaction bundles.
# Requires Java 17+. 100% synthetic data: no real patient data is ever involved.
#
#   ./synthea/generate.sh                   # 100 patients (quick test)
#   POPULATION=1000 ./synthea/generate.sh   # full run
#
# Output goes to synthea/output/fhir, wherever the script is called from.
set -euo pipefail

cd "$(dirname "$0")"

POPULATION="${POPULATION:-100}"
SEED="${SEED:-42}"                # fixed seed and pinned version => the same dataset every time
YEARS="${YEARS_OF_HISTORY:-10}"   # less history => smaller bundles, faster load
STATE="${STATE:-Massachusetts}"
OUT_DIR="${OUT_DIR:-$PWD/output}"
SYNTHEA_VERSION="${SYNTHEA_VERSION:-v4.0.0}"
JAR="synthea-${SYNTHEA_VERSION}.jar"
JAR_URL="https://github.com/synthetichealth/synthea/releases/download/${SYNTHEA_VERSION}/synthea-with-dependencies.jar"

if ! command -v java >/dev/null 2>&1; then
  echo "Java 17 or newer is required: brew install openjdk@21 (macOS) or apt install openjdk-21-jre (Ubuntu)" >&2
  exit 1
fi

if [[ ! -f "$JAR" ]]; then
  echo "Downloading Synthea ${SYNTHEA_VERSION}..."
  curl -fsSL -o "$JAR.tmp" "$JAR_URL"
  mv "$JAR.tmp" "$JAR"
fi

java -jar "$JAR" \
  -p "$POPULATION" \
  -s "$SEED" \
  --exporter.baseDirectory="$OUT_DIR" \
  --exporter.years_of_history="$YEARS" \
  --exporter.fhir.export=true \
  --exporter.fhir.transaction_bundle=true \
  --exporter.hospital.fhir.export=true \
  --exporter.practitioner.fhir.export=true \
  --exporter.csv.export=false \
  --generate.only_alive_patients=false \
  "$STATE"

COUNT=$(find "$OUT_DIR/fhir" -name '*.json' | wc -l | tr -d ' ')
SIZE=$(du -sh "$OUT_DIR/fhir" | cut -f1)
echo "Done: $COUNT bundles, $SIZE in $OUT_DIR/fhir"
