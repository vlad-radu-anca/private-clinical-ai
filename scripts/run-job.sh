#!/usr/bin/env bash
# Run one of the on-demand Kubernetes jobs (k8s/jobs/<name>) and follow its logs until it finishes.
#   scripts/run-job.sh index
#   scripts/run-job.sh eval
#
# index embeds every chunk on the GPU, which vLLM normally holds (one GPU, quota for one node). So it
# stops vLLM, runs the GPU embeddings server and the job, then removes that server and restarts vLLM,
# whether the job succeeded or not.
set -euo pipefail
cd "$(dirname "$0")/.."
job="${1:?usage: run-job.sh index|eval}"
kube=(kubectl -n clinical-ai)

scripts/k8s-overlay.sh >/dev/null
# A Job's pod template cannot be changed in place, so the previous run is removed first.
"${kube[@]}" delete job "$job" --ignore-not-found --wait=true

if [[ "$job" == index ]]; then
  restore_vllm() {
    echo "giving the GPU back to vLLM"
    "${kube[@]}" delete deployment tei-gpu --ignore-not-found --wait=true
    "${kube[@]}" scale deployment vllm --replicas=1
  }
  trap restore_vllm EXIT
  echo "freeing the GPU: stopping vLLM for the duration of the index"
  "${kube[@]}" scale deployment vllm --replicas=0
  "${kube[@]}" wait --for=delete pod -l app=vllm --timeout=5m
fi

kubectl apply -k "k8s/build/$job"
if [[ "$job" == index ]]; then
  "${kube[@]}" rollout status deployment/tei-gpu --timeout=10m
fi
"${kube[@]}" wait --for=condition=Ready pod -l "job-name=$job" --timeout=5m
"${kube[@]}" logs -f "job/$job"

# Report the job's own result, not just the end of its log stream.
"${kube[@]}" wait --for=condition=Complete "job/$job" --timeout=60s
