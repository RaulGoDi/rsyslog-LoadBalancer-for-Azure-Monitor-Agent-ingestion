#!/usr/bin/env bash
set -Eeuo pipefail

exec > >(tee -a /var/log/ama-lab-forwarder-bootstrap.log) 2>&1

dnf install -y rsyslog python3 firewalld curl
systemctl enable --now firewalld
firewall-cmd --permanent --add-port=514/tcp
firewall-cmd --reload
systemctl enable --now rsyslog

targets_file="/run/ama-lab-collector-targets.txt"
/usr/local/sbin/discover-collector-ips.py \
  --config /etc/ama-lab/collector-discovery.json \
  --output "$targets_file"

mapfile -t targets <"$targets_file"
if ((${#targets[@]} != 2)); then
  echo "ERROR: Expected exactly two collector targets, got ${#targets[@]}." >&2
  exit 1
fi

/usr/local/sbin/configure-rsyslog-target-pool.sh \
  --destination-count 2 \
  --destinations "${targets[@]}"
