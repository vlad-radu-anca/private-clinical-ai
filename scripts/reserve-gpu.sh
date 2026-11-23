#!/usr/bin/env bash
# Hold one GPU with an On-Demand Capacity Reservation before deploying anything. GPU capacity runs out,
# per zone and per region; creating a reservation is the quickest honest check (it fails at once, at no
# cost, when there is none) and, when it succeeds, the GPU is ours until it is cancelled.
#
#   scripts/reserve-gpu.sh ["regions"] ["instance types"]
#   scripts/reserve-gpu.sh "eu-west-1 eu-central-1 us-west-2" "g6.xlarge g5.xlarge"     (the defaults: EU first)
#
# Tries every zone that offers each type, in the order given, and stops at the first success. Prints the
# result as key=value lines (region, type, az, id) for $GITHUB_OUTPUT or a shell; exits 1 if nothing was
# free. A reservation is billed like a running instance until cancelled:
#   aws ec2 cancel-capacity-reservation --region <region> --capacity-reservation-id <id>
# Every region tried needs "Running On-Demand G and VT instances" quota (4 vCPUs for one *.xlarge).
#
# The reservation also expires on its own after RESERVE_HOURS (default 8), so a run that dies before its
# cleanup stops paying for it. An instance already running in it keeps running after that, unreserved.
set -euo pipefail
regions="${1:-eu-west-1 eu-central-1 us-west-2}"
types="${2:-g6.xlarge g5.xlarge}"
hours="${RESERVE_HOURS:-8}"
# GNU date on the runners, BSD date on a Mac.
end=$(date -u -d "+${hours} hours" +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || date -u -v+"${hours}"H +%Y-%m-%dT%H:%M:%SZ)

for region in $regions; do
  for type in $types; do
    zones=$(aws ec2 describe-instance-type-offerings --region "$region" --location-type availability-zone \
      --filters "Name=instance-type,Values=$type" --query 'InstanceTypeOfferings[].Location' --output text)
    for az in $zones; do
      if id=$(aws ec2 create-capacity-reservation --region "$region" \
          --instance-type "$type" --instance-platform Linux/UNIX --availability-zone "$az" \
          --instance-count 1 --instance-match-criteria open --end-date-type limited --end-date "$end" \
          --tag-specifications 'ResourceType=capacity-reservation,Tags=[{Key=Project,Value=clinical-ai}]' \
          --query 'CapacityReservation.CapacityReservationId' --output text 2>/tmp/reserve-gpu.err); then
        echo "reserved $type in $az" >&2
        printf 'region=%s\ntype=%s\naz=%s\nid=%s\n' "$region" "$type" "$az" "$id"
        exit 0
      fi
      # One line per attempt: InsufficientInstanceCapacity is the usual answer; anything else (quota,
      # permissions) is worth reading.
      echo "no $type in $az: $(grep -oE '\(([A-Za-z]+)\)' /tmp/reserve-gpu.err | head -1 || echo unknown error)" >&2
    done
  done
done

echo "no GPU free in: $regions ($types)" >&2
exit 1
