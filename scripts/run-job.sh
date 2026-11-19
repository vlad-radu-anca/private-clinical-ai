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
  # shellcheck disable=SC2329  # called by the EXIT trap below
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

if [[ "$job" == index ]]; then
  # The embeddings server must be serving before the job starts: the job fails on its first refused
  # connection (backoffLimit 0), and on a fresh node the image pull alone takes minutes.
  kubectl apply -k "k8s/build/$job" -l app=tei-gpu
  "${kube[@]}" rollout status deployment/tei-gpu --timeout=20m
fi
kubectl apply -k "k8s/build/$job"

# Follow the logs as soon as the container has started. Waiting for "Ready" would hang on a job that
# finishes or fails quickly, because a finished pod is never Ready.
echo "waiting for the $job pod to start"
for _ in $(seq 60); do
  "${kube[@]}" logs "job/$job" --tail=1 >/dev/null 2>&1 && break
  sleep 5
done
# Ctrl+C stops following the logs, not the job: the job runs in the cluster either way. Without this,
# Ctrl+C ended the script, whose cleanup removed the GPU embeddings server under a running index job.
trap 'echo; echo "stopped following the logs; the $job job keeps running, waiting for it to finish"' INT
"${kube[@]}" logs -f "job/$job" || true
trap - INT

# Report the job's own result, not just the end of its log stream. Poll both outcomes: waiting for
# "Complete" alone would sit out the whole timeout on a failed job.
for _ in $(seq 360); do
  status=$("${kube[@]}" get job "$job" -o jsonpath='{.status.succeeded}/{.status.failed}')
  case "$status" in
    1/*) echo "$job job complete"; exit 0 ;;
    */[1-9]*) echo "$job job failed: kubectl -n clinical-ai logs job/$job" >&2; exit 1 ;;
  esac
  sleep 10
done
echo "$job job still running after an hour; leaving it, check: kubectl -n clinical-ai get job $job" >&2
exit 1
