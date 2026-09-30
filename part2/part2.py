#!/usr/bin/env python3
"""Part 2 - Clone a machine.

1. Snapshot the boot disk of the Part 1 instance -> base-snapshot-<instance>
2. Create three instances from that snapshot, timing each one
3. Write the times to TIMING.md
"""

import argparse
import os
import time

import google.auth
import googleapiclient.discovery
from googleapiclient.errors import HttpError

credentials, project = google.auth.default()
project = project or os.environ.get('GOOGLE_CLOUD_PROJECT')
service = googleapiclient.discovery.build('compute', 'v1', credentials=credentials)

ZONE = 'us-west1-b'
INSTANCE = 'flask-tutorial-vm'          # instance created in Part 1
SNAPSHOT = 'base-snapshot-' + INSTANCE
MACHINE_TYPE = 'e2-micro'               # same type as Part 1
TAG = 'allow-5000'


def list_instances(compute, project, zone):
    result = compute.instances().list(project=project, zone=zone).execute()
    return result['items'] if 'items' in result else []


def wait_for_operation(compute, project, zone, operation):
    """Block until a zonal operation is DONE; raise on error."""
    while True:
        try:
            result = compute.zoneOperations().get(
                project=project, zone=zone, operation=operation['name']).execute()
        except (TimeoutError, OSError):
            time.sleep(2)
            continue
        if result['status'] == 'DONE':
            if 'error' in result:
                raise RuntimeError(result['error'])
            return result
        time.sleep(2)


def wait_for_snapshot_ready(compute, project, name):
    """Block until the snapshot status is READY."""
    while True:
        try:
            snap = compute.snapshots().get(project=project, snapshot=name).execute()
        except (TimeoutError, OSError):
            time.sleep(3)
            continue
        if snap.get('status') == 'READY':
            return
        print('  snapshot status: %s ...' % snap.get('status'))
        time.sleep(5)


def get_boot_disk_name(compute, project, zone, instance):
    """Find the boot disk that belongs to the instance."""
    inst = compute.instances().get(project=project, zone=zone, instance=instance).execute()
    for disk in inst['disks']:
        if disk.get('boot'):
            return disk['source'].split('/')[-1]
    raise RuntimeError('No boot disk found on ' + instance)


def snapshot_exists(compute, project, name):
    try:
        compute.snapshots().get(project=project, snapshot=name).execute()
        return True
    except HttpError as e:
        if e.resp.status == 404:
            return False
        raise


def create_snapshot(compute, project, zone, disk, name):
    print('Creating snapshot %s from disk %s...' % (name, disk))
    op = compute.disks().createSnapshot(
        project=project, zone=zone, disk=disk, body={'name': name}).execute()
    wait_for_operation(compute, project, zone, op)
    print('Snapshot ready.')


def create_instance_from_snapshot(compute, project, zone, name, snapshot, machine_type):
    body = {
        'name': name,
        'machineType': 'zones/%s/machineTypes/%s' % (zone, machine_type),
        'tags': {'items': [TAG]},
        'disks': [{
            'boot': True,
            'autoDelete': True,
            'initializeParams': {
                'sourceSnapshot': 'global/snapshots/' + snapshot,
            },
        }],
        'networkInterfaces': [{
            'network': 'global/networks/default',
            'accessConfigs': [{'type': 'ONE_TO_ONE_NAT', 'name': 'External NAT'}],
        }],
    }
    op = compute.instances().insert(project=project, zone=zone, body=body).execute()
    wait_for_operation(compute, project, zone, op)


def get_external_ip(compute, project, zone, name):
    inst = compute.instances().get(project=project, zone=zone, instance=name).execute()
    return inst['networkInterfaces'][0]['accessConfigs'][0].get('natIP')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--machine-type', default=MACHINE_TYPE)
    args = parser.parse_args()

    print('Project:', project)
    print('Your running instances are:')
    for inst in list_instances(service, project, ZONE):
        print('  ' + inst['name'])

    # Step 1: snapshot (skip if it already exists)
    if snapshot_exists(service, project, SNAPSHOT):
        print('Snapshot %s already exists, reusing it.' % SNAPSHOT)
    else:
        disk = get_boot_disk_name(service, project, ZONE, INSTANCE)
        create_snapshot(service, project, ZONE, disk, SNAPSHOT)
    wait_for_snapshot_ready(service, project, SNAPSHOT)

    # Step 2: create three instances from the snapshot and time each
    timings = []
    for i in range(1, 4):
        name = 'flask-clone-%d' % i
        print('Creating %s...' % name)
        start = time.time()
        create_instance_from_snapshot(service, project, ZONE, name, SNAPSHOT, args.machine_type)
        elapsed = time.time() - start
        timings.append((name, elapsed))
        print('  %s created in %.2f seconds' % (name, elapsed))

    # Step 3: write TIMING.md next to this script
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'TIMING.md')
    with open(path, 'w') as f:
        f.write('# Timing\n\n')
        f.write('Time to create an instance from `%s` (%s, %s):\n\n' %
                (SNAPSHOT, args.machine_type, ZONE))
        f.write('| Instance | Time (seconds) |\n|---|---|\n')
        for name, secs in timings:
            f.write('| %s | %.2f |\n' % (name, secs))
        f.write('\nAverage: %.2f seconds\n' % (sum(s for _, s in timings) / len(timings)))
    print('Wrote', path)

    for name, _ in timings:
        print('%s: http://%s:5000' % (name, get_external_ip(service, project, ZONE, name)))


if __name__ == '__main__':
    main()
