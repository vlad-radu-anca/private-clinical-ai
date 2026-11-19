#!/usr/bin/env python3
"""Load Synthea FHIR transaction bundles into a FHIR server (e.g. HAPI).

Order matters: Synthea patient bundles reference Organizations and Practitioners
with conditional references, so hospitalInformation*.json and
practitionerInformation*.json are loaded first, sequentially, then patients in parallel.

Source can be a local directory or s3://bucket/prefix (needs boto3).
Logs never contain patient names: Synthea file names embed them, so only a
short hash of each file name is logged (good habit even with synthetic data).

  python load_fhir.py --source ../synthea/output/fhir --fhir-url http://localhost:8080/fhir
  python load_fhir.py --source s3://my-bucket/synthea/run-1/ --fhir-url http://internal-alb/fhir
"""

import argparse
import concurrent.futures as cf
import hashlib
import json
import logging
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

log = logging.getLogger("loader")
PRIORITY_PREFIXES = ("hospitalInformation", "practitionerInformation")


def base(name: str) -> str:
    """File name without any directory or S3 prefix."""
    return name.rsplit("/", 1)[-1]


def safe_id(name: str) -> str:
    """Opaque identifier for logs, so file names (which contain patient names) are never printed."""
    return hashlib.sha256(name.encode()).hexdigest()[:12]


class LocalSource:
    def __init__(self, path):
        self.path = path

    def list(self):
        return sorted(f for f in os.listdir(self.path) if f.endswith(".json"))

    def read(self, name):
        with open(os.path.join(self.path, name), "rb") as fh:
            return fh.read()


class S3Source:
    def __init__(self, uri):
        import boto3  # only needed for S3 sources

        bucket, _, prefix = uri[len("s3://") :].partition("/")
        self.bucket, self.prefix = bucket, prefix
        self.s3 = boto3.client("s3")

    def list(self):
        names = []
        for page in self.s3.get_paginator("list_objects_v2").paginate(Bucket=self.bucket, Prefix=self.prefix):
            names += [o["Key"] for o in page.get("Contents", []) if o["Key"].endswith(".json")]
        return sorted(names)

    def read(self, key):
        return self.s3.get_object(Bucket=self.bucket, Key=key)["Body"].read()


SYNTHEA_SYSTEM = "https://github.com/synthetichealth/synthea"


def patient_key(bundle):
    """The Synthea identifier of the bundle's Patient, or None for bundles without one."""
    for entry in bundle.get("entry", []):
        resource = entry.get("resource", {})
        if resource.get("resourceType") == "Patient":
            for ident in resource.get("identifier", []):
                if ident.get("system") == SYNTHEA_SYSTEM and ident.get("value"):
                    return ident["value"]
    return None


def patient_exists(fhir_url, key, timeout):
    """True if the server already holds a Patient with this Synthea identifier."""
    query = urllib.parse.urlencode({"identifier": f"{SYNTHEA_SYSTEM}|{key}", "_summary": "count"})
    req = urllib.request.Request(f"{fhir_url.rstrip('/')}/Patient?{query}", headers={"Accept": "application/fhir+json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.load(resp).get("total", 0) > 0


def post_bundle(fhir_url, body, retries, timeout, key=None):
    """POST a transaction bundle. Synthea creates every resource with a plain POST, so sending a bundle
    twice duplicates the patient. A gateway timeout does not mean the server failed: it may still finish
    the transaction. So before every retry, and before the first attempt, the server is asked whether
    the patient is already there; if so, the bundle counts as loaded. This also makes re-runs resume."""
    req = urllib.request.Request(
        fhir_url.rstrip("/"),
        data=body,
        method="POST",
        headers={"Content-Type": "application/fhir+json", "Accept": "application/fhir+json"},
    )
    err = None
    for attempt in range(1, retries + 1):
        if key:
            try:
                if patient_exists(fhir_url, key, timeout):
                    return "exists" if attempt == 1 else "committed"
            except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
                err = f"existence check: {type(e).__name__}"
                if attempt < retries:
                    time.sleep(min(2**attempt, 30))
                continue
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.status
        except urllib.error.HTTPError as e:
            if e.code < 500 and e.code != 429:  # client error: retrying will not help
                raise RuntimeError(f"HTTP {e.code}") from None
            err = f"HTTP {e.code}"
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            err = type(e).__name__
        if attempt < retries:
            time.sleep(min(2**attempt, 30))
    raise RuntimeError(f"failed after {retries} attempts ({err})")


def load_one(source, name, args):
    """Returns (outcome, seconds); outcome is "loaded", "exists" (skipped) or "committed" (a timed-out
    attempt that the server finished anyway)."""
    body = source.read(name)
    key = patient_key(json.loads(body))  # also fails fast on corrupt files
    start = time.monotonic()
    outcome = post_bundle(args.fhir_url, body, args.retries, args.timeout, key)
    return outcome if outcome in ("exists", "committed") else "loaded", time.monotonic() - start


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--source", required=True, help="local dir or s3://bucket/prefix")
    p.add_argument("--fhir-url", default=os.getenv("FHIR_URL", "http://localhost:8080/fhir"))
    p.add_argument("--workers", type=int, default=4)
    p.add_argument("--retries", type=int, default=5)
    p.add_argument("--timeout", type=int, default=300, help="seconds per bundle")
    p.add_argument("--limit", type=int, default=0, help="load only N patient bundles (0 = all)")
    args = p.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    source = S3Source(args.source) if args.source.startswith("s3://") else LocalSource(args.source)

    names = source.list()
    priority = [n for n in names if base(n).startswith(PRIORITY_PREFIXES)]
    patients = [n for n in names if n not in priority]
    if args.limit:
        patients = patients[: args.limit]
    log.info("found %d reference bundles, %d patient bundles", len(priority), len(patients))

    # 1) Organizations / Practitioners first, sequentially
    for name in priority:
        _, secs = load_one(source, name, args)
        log.info("loaded reference bundle %s in %.1fs", base(name), secs)

    # 2) Patients in parallel
    ok, skipped, failed = 0, 0, []
    with cf.ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(load_one, source, n, args): n for n in patients}
        for i, fut in enumerate(cf.as_completed(futures), 1):
            name = futures[fut]
            try:
                outcome, _ = fut.result()
                if outcome == "exists":
                    skipped += 1
                else:
                    ok += 1
                    if outcome == "committed":
                        log.info("bundle %s: the server finished a timed-out attempt; not resent", safe_id(name))
            except Exception as e:  # noqa: BLE001
                failed.append(name)
                log.error("bundle %s failed: %s", safe_id(name), e)
            if i % 25 == 0 or i == len(patients):
                log.info(
                    "progress %d/%d (ok=%d, already loaded=%d, failed=%d)", i, len(patients), ok, skipped, len(failed)
                )

    if failed:
        with open("failed_bundles.txt", "w") as fh:
            fh.write("\n".join(failed) + "\n")
        log.warning("%d bundles failed; list written to failed_bundles.txt", len(failed))
    log.info("done: %d loaded, %d already loaded, %d failed", ok, skipped, len(failed))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
