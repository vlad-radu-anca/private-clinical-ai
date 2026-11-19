"""Tests for the loader, run against a fake FHIR server on localhost.

The fake server records every bundle it receives and can fail on purpose, so
the tests cover the behaviour that matters when loading real volumes: order,
retries on transient errors, no retries on client errors, and logs that never
contain patient names.
"""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import pytest

import load_fhir

# Synthea names patient files after the (synthetic) patient.
PATIENTS = ["Alice_Smith_1111.json", "Bob_Jones_2222.json", "Carol_White_3333.json"]
REFERENCE = ["hospitalInformation1700000000000.json", "practitionerInformation1700000000000.json"]


class FakeFhir:
    """Records received bundles. `plan` maps a bundle id to the status codes to return, in order.
    Bundle ids in `commit_anyway` are stored even when an error is returned, like a transaction that
    the server finished after the load balancer had already given up (HTTP 504)."""

    def __init__(self):
        self.received = []
        self.plan = {}
        self.commit_anyway = set()
        self.patients = []  # Synthea identifiers of stored Patient resources
        self.lock = threading.Lock()

    def handler(self):
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                bundle_id = body["id"]
                with fake.lock:
                    codes = fake.plan.get(bundle_id, [])
                    status = codes.pop(0) if codes else 200
                    if status == 200 or bundle_id in fake.commit_anyway:
                        fake.received.append(bundle_id)
                        key = load_fhir.patient_key(body)
                        if key:
                            fake.patients.append(key)
                self.send_response(status)
                self.end_headers()

            def do_GET(self):
                query = parse_qs(urlparse(self.path).query)
                key = query["identifier"][0].split("|", 1)[1]
                with fake.lock:
                    total = fake.patients.count(key)
                payload = json.dumps({"resourceType": "Bundle", "total": total}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/fhir+json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *args):
                pass

        return Handler


@pytest.fixture
def fhir():
    fake = FakeFhir()
    server = ThreadingHTTPServer(("127.0.0.1", 0), fake.handler())
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    fake.url = f"http://127.0.0.1:{server.server_port}/fhir"
    yield fake
    server.shutdown()


@pytest.fixture
def bundles(tmp_path):
    for name in PATIENTS + REFERENCE:
        (tmp_path / name).write_text(json.dumps({"resourceType": "Bundle", "id": name}))
    return tmp_path


@pytest.fixture(autouse=True)
def no_backoff(monkeypatch):
    monkeypatch.setattr(load_fhir.time, "sleep", lambda _seconds: None)


def run(monkeypatch, tmp_path, source, url, *extra):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("sys.argv", ["load_fhir.py", "--source", str(source), "--fhir-url", url, *extra])
    return load_fhir.main()


def test_reference_bundles_load_before_any_patient(monkeypatch, tmp_path, fhir, bundles):
    assert run(monkeypatch, tmp_path, bundles, fhir.url) == 0

    assert sorted(fhir.received[:2]) == sorted(REFERENCE)
    assert sorted(fhir.received[2:]) == sorted(PATIENTS)


def test_transient_errors_are_retried(monkeypatch, tmp_path, fhir, bundles):
    fhir.plan = {"Bob_Jones_2222.json": [503, 429, 500]}

    assert run(monkeypatch, tmp_path, bundles, fhir.url, "--retries", "5") == 0

    assert "Bob_Jones_2222.json" in fhir.received


def test_client_errors_fail_without_retrying(monkeypatch, tmp_path, fhir, bundles):
    fhir.plan = {"Bob_Jones_2222.json": [400, 200]}

    assert run(monkeypatch, tmp_path, bundles, fhir.url) == 1

    assert "Bob_Jones_2222.json" not in fhir.received
    assert (tmp_path / "failed_bundles.txt").read_text().split() == ["Bob_Jones_2222.json"]


def test_logs_never_contain_patient_names(monkeypatch, tmp_path, fhir, bundles, caplog):
    fhir.plan = {"Carol_White_3333.json": [400]}

    run(monkeypatch, tmp_path, bundles, fhir.url)

    for name in ("Alice", "Bob", "Carol", "Smith", "Jones", "White"):
        assert name not in caplog.text
    assert load_fhir.safe_id("Carol_White_3333.json") in caplog.text


def write_patient_bundles(path):
    """Bundles shaped like Synthea's: a Patient with the Synthea identifier, created by plain POST."""
    for i, name in enumerate(PATIENTS):
        ident = {"system": load_fhir.SYNTHEA_SYSTEM, "value": f"uuid-{i}"}
        patient = {"resourceType": "Patient", "identifier": [ident]}
        bundle = {"resourceType": "Bundle", "id": name, "entry": [{"resource": patient, "request": {"method": "POST"}}]}
        (path / name).write_text(json.dumps(bundle))


def test_timed_out_bundle_that_the_server_finished_is_not_sent_again(monkeypatch, tmp_path, fhir, bundles):
    write_patient_bundles(bundles)
    fhir.plan = {"Bob_Jones_2222.json": [504]}
    fhir.commit_anyway = {"Bob_Jones_2222.json"}

    assert run(monkeypatch, tmp_path, bundles, fhir.url) == 0

    assert fhir.received.count("Bob_Jones_2222.json") == 1
    assert sorted(fhir.patients) == ["uuid-0", "uuid-1", "uuid-2"]


def test_second_run_skips_patients_already_loaded(monkeypatch, tmp_path, fhir, bundles, caplog):
    caplog.set_level("INFO")
    write_patient_bundles(bundles)
    assert run(monkeypatch, tmp_path, bundles, fhir.url) == 0

    assert run(monkeypatch, tmp_path, bundles, fhir.url) == 0

    assert sorted(fhir.patients) == ["uuid-0", "uuid-1", "uuid-2"]
    assert "0 loaded, 3 already loaded, 0 failed" in caplog.text


def test_limit_applies_to_patients_only(monkeypatch, tmp_path, fhir, bundles):
    assert run(monkeypatch, tmp_path, bundles, fhir.url, "--limit", "1") == 0

    assert sorted(fhir.received[:2]) == sorted(REFERENCE)
    assert len(fhir.received) == 3
