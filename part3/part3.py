#!/usr/bin/env python3

import sys
import time
import urllib.request
from pathlib import Path

import google.auth
import google_auth_httplib2
import googleapiclient.discovery
import httplib2
from googleapiclient.errors import HttpError

ZONE = "us-west1-a"
MACHINE_TYPE = "e2-micro"
VM1 = "lab5-vm1"
VM2 = "lab5-vm2"

# This program runs in two places:
# Cloud Shell: create VM-1.
# VM-1: create VM-2.
ON_VM1 = "--launch-vm2" in sys.argv


def metadata(key):
    request = urllib.request.Request(
        "http://metadata.google.internal/computeMetadata/v1/"
        f"instance/attributes/{key}",
        headers={"Metadata-Flavor": "Google"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read().decode()


credentials, default_project = google.auth.default(
    scopes=["https://www.googleapis.com/auth/cloud-platform"]
)

PROJECT = metadata("project") if ON_VM1 else default_project
if not PROJECT:
    raise RuntimeError("No project found. Set the Cloud Shell project first.")

transport = google_auth_httplib2.AuthorizedHttp(
    credentials, http=httplib2.Http(timeout=120)
)
compute = googleapiclient.discovery.build(
    "compute", "v1", http=transport, cache_discovery=False
)


def wait(operation):
    deadline = time.monotonic() + 900

    while time.monotonic() < deadline:
        if "zone" in operation:
            request = compute.zoneOperations().get(
                project=PROJECT,
                zone=operation["zone"].split("/")[-1],
                operation=operation["name"],
            )
        else:
            request = compute.globalOperations().get(
                project=PROJECT,
                operation=operation["name"],
            )

        result = request.execute(num_retries=3)

        if result["status"] == "DONE":
            if "error" in result:
                raise RuntimeError(result["error"])
            return

        time.sleep(3)

    raise TimeoutError(f"Operation timed out: {operation['name']}")


def ensure_firewall():
    try:
        compute.firewalls().get(
            project=PROJECT, firewall="allow-5000"
        ).execute(num_retries=3)
        return
    except HttpError as error:
        if error.resp.status != 404:
            raise

    operation = compute.firewalls().insert(
        project=PROJECT,
        body={
            "name": "allow-5000",
            "network": f"projects/{PROJECT}/global/networks/default",
            "direction": "INGRESS",
            "sourceRanges": ["0.0.0.0/0"],
            "targetTags": ["allow-5000"],
            "allowed": [{"IPProtocol": "tcp", "ports": ["5000"]}],
        },
    ).execute()
    wait(operation)


def create_vm(name, startup, extra_metadata=None, account=None):
    try:
        existing = compute.instances().get(
            project=PROJECT, zone=ZONE, instance=name
        ).execute(num_retries=3)
        print(f"{name} already exists; not creating another.", flush=True)
        return existing
    except HttpError as error:
        if error.resp.status != 404:
            raise

    image = compute.images().getFromFamily(
        project="ubuntu-os-cloud", family="ubuntu-2204-lts"
    ).execute(num_retries=3)

    items = [{"key": "startup-script", "value": startup}]
    items.extend(extra_metadata or [])

    body = {
        "name": name,
        "machineType": f"zones/{ZONE}/machineTypes/{MACHINE_TYPE}",
        "disks": [{
            "boot": True,
            "autoDelete": True,
            "initializeParams": {
                "sourceImage": image["selfLink"],
                "diskSizeGb": "10",
            },
        }],
        "networkInterfaces": [{
            "network": f"projects/{PROJECT}/global/networks/default",
            "accessConfigs": [{
                "name": "External NAT",
                "type": "ONE_TO_ONE_NAT",
            }],
        }],
        "metadata": {"items": items},
        # VM-2 gets no service account or launcher credentials.
        "serviceAccounts": [],
    }

    if account:
        body["serviceAccounts"] = [{
            "email": account,
            "scopes": ["https://www.googleapis.com/auth/cloud-platform"],
        }]

    if name == VM2:
        body["tags"] = {"items": ["allow-5000"]}

    print(f"Creating {name} in project {PROJECT}...", flush=True)
    operation = compute.instances().insert(
        project=PROJECT, zone=ZONE, body=body
    ).execute()
    wait(operation)

    return compute.instances().get(
        project=PROJECT, zone=ZONE, instance=name
    ).execute(num_retries=3)


def main():
    if ON_VM1:
        print("This Python program is running inside VM-1.", flush=True)
        vm2 = create_vm(VM2, metadata("vm2-startup-script"))
        ip = vm2["networkInterfaces"][0]["accessConfigs"][0]["natIP"]
        print(f"VM-2 Flask URL: http://{ip}:5000", flush=True)
        return

    ensure_firewall()
    folder = Path(__file__).resolve().parent

    vm1_startup = """#!/bin/bash
set -euxo pipefail
apt-get update
apt-get install -y python3 python3-pip python3-venv curl
mkdir -p /srv/lab5
cd /srv/lab5

curl -fsS -H "Metadata-Flavor: Google" \
  http://metadata.google.internal/computeMetadata/v1/instance/attributes/vm1-launch-code \
  -o part3.py

python3 -m venv .venv
.venv/bin/pip install google-api-python-client google-auth google-auth-httplib2
.venv/bin/python -u part3.py --launch-vm2
"""

    create_vm(
        VM1,
        vm1_startup,
        extra_metadata=[
            {"key": "project", "value": PROJECT},
            {"key": "vm1-launch-code", "value": Path(__file__).read_text()},
            {
                "key": "vm2-startup-script",
                "value": (folder / "vm2-startup-script.sh").read_text(),
            },
        ],
        account=f"lab5-launcher@{PROJECT}.iam.gserviceaccount.com",
    )
    print("VM-1 created. Its startup script will create VM-2.", flush=True)


if __name__ == "__main__":
    main()
