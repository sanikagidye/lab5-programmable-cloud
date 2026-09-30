#!/usr/bin/env python3
"""
Lab 5, Part 1 (extra credit version) using the Cloud Client Library
`google-cloud-compute` (google.cloud.compute_v1) instead of the older
google-api-python-client.

Install:  pip3 install google-cloud-compute

Portions adapted from the Google Cloud Python samples
(GoogleCloudPlatform/python-docs-samples, compute/), Copyright Google LLC,
licensed under the Apache License 2.0.
"""

import argparse

import google.auth
from google.api_core.exceptions import NotFound
from google.cloud import compute_v1

ZONE = 'us-west1-b'
INSTANCE_NAME = 'flask-tutorial-vm'
MACHINE_TYPE = 'e2-micro'            # use e2-medium while developing if too slow
IMAGE_PROJECT = 'ubuntu-os-cloud'
IMAGE_FAMILY = 'ubuntu-2204-lts'
FIREWALL_NAME = 'allow-5000'
NETWORK_TAG = 'allow-5000'

STARTUP_SCRIPT = """#!/bin/bash
# Startup scripts run as root, from /, with a minimal environment.
export HOME=/root
apt-get update
apt-get install -y python3 python3-pip python3-setuptools git

mkdir -p /opt/app
cd /opt/app
git clone https://github.com/cu-csci-4253-datacenter/flask-tutorial
cd flask-tutorial

python3 setup.py install
pip3 install -e .

export FLASK_APP=flaskr
flask init-db
nohup flask run -h 0.0.0.0 &
"""


def wait_for(operation, what):
    """Block until a long-running operation finishes; raise on error."""
    print(f'Waiting for {what}...')
    operation.result()          # blocks; raises if the operation failed
    if operation.error_code:
        raise RuntimeError(
            f'{what} failed: [{operation.error_code}] {operation.error_message}')


def list_instances(project, zone):
    client = compute_v1.InstancesClient()
    return list(client.list(project=project, zone=zone))


def firewall_rule_exists(project, name):
    client = compute_v1.FirewallsClient()
    try:
        client.get(project=project, firewall=name)
        return True
    except NotFound:
        return False


def create_firewall_rule(project):
    rule = compute_v1.Firewall(
        name=FIREWALL_NAME,
        direction='INGRESS',
        network='global/networks/default',
        source_ranges=['0.0.0.0/0'],
        target_tags=[NETWORK_TAG],
        allowed=[compute_v1.Allowed(I_p_protocol='tcp', ports=['5000'])],
    )
    client = compute_v1.FirewallsClient()
    op = client.insert(project=project, firewall_resource=rule)
    wait_for(op, f'firewall rule {FIREWALL_NAME}')


def create_instance(project, zone, name, machine_type):
    # Newest image in the family, so no hard-coded image name.
    image = compute_v1.ImagesClient().get_from_family(
        project=IMAGE_PROJECT, family=IMAGE_FAMILY)

    disk = compute_v1.AttachedDisk(
        boot=True,
        auto_delete=True,
        initialize_params=compute_v1.AttachedDiskInitializeParams(
            source_image=image.self_link),
    )

    access = compute_v1.AccessConfig(
        type_=compute_v1.AccessConfig.Type.ONE_TO_ONE_NAT.name,
        name='External NAT',
    )
    nic = compute_v1.NetworkInterface(
        network='global/networks/default',
        access_configs=[access],
    )

    instance = compute_v1.Instance(
        name=name,
        machine_type=f'zones/{zone}/machineTypes/{machine_type}',
        disks=[disk],
        network_interfaces=[nic],
        metadata=compute_v1.Metadata(
            items=[compute_v1.Items(key='startup-script',
                                    value=STARTUP_SCRIPT)]),
    )

    client = compute_v1.InstancesClient()
    op = client.insert(project=project, zone=zone, instance_resource=instance)
    wait_for(op, f'instance {name}')


def set_tags(project, zone, name, tags):
    client = compute_v1.InstancesClient()
    # setTags needs the current tags fingerprint (optimistic locking).
    instance = client.get(project=project, zone=zone, instance=name)
    tags_resource = compute_v1.Tags(
        items=tags, fingerprint=instance.tags.fingerprint)
    op = client.set_tags(project=project, zone=zone, instance=name,
                         tags_resource=tags_resource)
    wait_for(op, 'network tags')


def get_external_ip(project, zone, name):
    instance = compute_v1.InstancesClient().get(
        project=project, zone=zone, instance=name)
    return instance.network_interfaces[0].access_configs[0].nat_i_p


def main(machine_type, name):
    _, project = google.auth.default()

    print(f'Project: {project}')
    print(f'Existing instances in {ZONE}:')
    for inst in list_instances(project, ZONE):
        print('  -', inst.name)

    # 1. Firewall rule (only create if it isn't already there)
    if firewall_rule_exists(project, FIREWALL_NAME):
        print(f'Firewall rule {FIREWALL_NAME} already exists.')
    else:
        print(f'Creating firewall rule {FIREWALL_NAME}...')
        create_firewall_rule(project)

    # 2. VM
    print(f'Creating instance {name} ({machine_type})...')
    create_instance(project, ZONE, name, machine_type)

    # 3. Network tag so the firewall rule applies to this VM
    print(f'Applying network tag {NETWORK_TAG}...')
    set_tags(project, ZONE, name, [NETWORK_TAG])

    # 4. Tell the user where to go
    ip = get_external_ip(project, ZONE, name)
    print('\nThe Flask application is available at:\n')
    print(f'    http://{ip}:5000\n')
    print('Note: the startup script needs a few minutes to install '
          'everything, so the page may not respond immediately.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--machine-type', default=MACHINE_TYPE)
    parser.add_argument('--name', default=INSTANCE_NAME)
    args = parser.parse_args()
    main(args.machine_type, args.name)
