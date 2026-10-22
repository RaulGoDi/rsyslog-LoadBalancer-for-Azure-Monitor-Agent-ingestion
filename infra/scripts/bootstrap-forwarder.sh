#!/usr/bin/env bash
set -Eeuo pipefail

exec > >(tee -a /var/log/ama-lab-forwarder-bootstrap.log) 2>&1

dnf install -y rsyslog python3 firewalld curl logrotate
systemctl enable --now firewalld
firewall-cmd --permanent --add-port=514/tcp
firewall-cmd --reload
systemctl enable --now rsyslog
install -d -m 0750 /var/log/ama-lab

cat >/etc/logrotate.d/ama-lab-forwarder <<'LOGROTATE'
/var/log/ama-lab/forwarder-received.log {
    size 128M
    rotate 4
    compress
    delaycompress
    missingok
    notifempty
    copytruncate
}
LOGROTATE
chmod 0644 /etc/logrotate.d/ama-lab-forwarder

cat >/etc/systemd/system/ama-lab-forwarder-logrotate.service <<'UNIT'
[Unit]
Description=Rotate the rsyslog forwarder lab capture log

[Service]
Type=oneshot
ExecStart=/usr/sbin/logrotate /etc/logrotate.d/ama-lab-forwarder
UNIT

cat >/etc/systemd/system/ama-lab-forwarder-logrotate.timer <<'UNIT'
[Unit]
Description=Check the rsyslog forwarder capture log size

[Timer]
OnBootSec=5min
OnUnitActiveSec=5min
Persistent=true

[Install]
WantedBy=timers.target
UNIT
systemctl daemon-reload
systemctl enable --now ama-lab-forwarder-logrotate.timer

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
