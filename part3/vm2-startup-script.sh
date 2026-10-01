#!/bin/bash
set -euxo pipefail

apt-get update
apt-get install -y python3 python3-pip python3-venv git

mkdir -p /opt/app
cd /opt/app

git clone https://github.com/cu-csci-4253-datacenter/flask-tutorial
cd flask-tutorial

python3 -m venv .venv
source .venv/bin/activate
pip install -e .

export FLASK_APP=flaskr
flask init-db

nohup flask run --host=0.0.0.0 --port=5000 \
  > /var/log/flask-app.log 2>&1 < /dev/null &
