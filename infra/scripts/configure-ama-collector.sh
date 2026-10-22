#!/usr/bin/env bash
set -Eeuo pipefail

log_dir="/var/log/ama-lab"
mkdir -p "$log_dir" /var/lib/ama-lab
exec > >(tee -a "${log_dir}/bootstrap.log") 2>&1

[[ $EUID -eq 0 ]] || { echo "ERROR: collector setup must run as root." >&2; exit 1; }
[[ -r /etc/redhat-release ]] || { echo "ERROR: this bootstrap expects a RHEL-family collector image." >&2; exit 1; }

dnf install -y rsyslog python3 firewalld curl logrotate sudo initscripts
systemctl enable --now firewalld
firewall-cmd --permanent --add-port=514/tcp
firewall-cmd --reload

installer="/var/lib/ama-lab/Forwarder_AMA_installer.py"
installer_url="https://raw.githubusercontent.com/Azure/Azure-Sentinel/master/DataConnectors/Syslog/Forwarder_AMA_installer.py"
installer_sha256="8acb22fe5df6810c392b88a1363ac22efc911b5d845db05bc1936b2c1bd15228"
curl --fail --show-error --silent --location "$installer_url" --output "${installer}.tmp"
test -s "${installer}.tmp"
if ! printf '%s  %s\n' "$installer_sha256" "${installer}.tmp" | sha256sum --check --status; then
  rm -f "${installer}.tmp"
  echo "ERROR: the documented Sentinel installer checksum changed; review and update the pinned checksum before continuing." >&2
  exit 1
fi
mv -f "${installer}.tmp" "$installer"
python3 "$installer"

install -d -m 0750 "$log_dir"
cat >/etc/rsyslog.d/20-ama-lab-capture.conf <<'RSYSLOG'
template(name="AmaLabCapture" type="string" string="%timegenerated:::date-rfc3339% %syslogfacility-text% %msg%\n")
if ($syslogfacility-text == "local6" or $syslogfacility-text == "local7") then {
    action(type="omfile" file="/var/log/ama-lab/received.log" template="AmaLabCapture")
}
RSYSLOG
chmod 0640 /etc/rsyslog.d/20-ama-lab-capture.conf

cat >/etc/logrotate.d/ama-lab <<'LOGROTATE'
/var/log/ama-lab/received.log {
    size 128M
    rotate 4
    compress
    delaycompress
    missingok
    notifempty
    copytruncate
}
LOGROTATE
chmod 0644 /etc/logrotate.d/ama-lab

cat >/etc/systemd/system/ama-lab-logrotate.service <<'UNIT'
[Unit]
Description=Rotate the rsyslog lab capture log

[Service]
Type=oneshot
ExecStart=/usr/sbin/logrotate /etc/logrotate.d/ama-lab
UNIT

cat >/etc/systemd/system/ama-lab-logrotate.timer <<'UNIT'
[Unit]
Description=Check the rsyslog lab capture log size

[Timer]
OnBootSec=5min
OnUnitActiveSec=5min
Persistent=true

[Install]
WantedBy=timers.target
UNIT

if ! rsyslogd -N1; then
  echo "ERROR: rsyslog configuration validation failed after the documented Sentinel installer and lab capture rule." >&2
  exit 1
fi
systemctl enable --now rsyslog
systemctl restart rsyslog
systemctl daemon-reload
systemctl enable --now ama-lab-logrotate.timer
echo "Collector listener and local6/local7 capture rule are active."
