#!/usr/bin/env bash
set -Eeuo pipefail

usage() {
  echo "Usage: $0 --destination-count N --destinations HOST [HOST ...]" >&2
}

fail() {
  echo "ERROR: $*" >&2
  exit 1
}

destination_count=""
destinations=()
while (($#)); do
  case "$1" in
    --destination-count)
      (($# >= 2)) || { usage; exit 2; }
      destination_count="$2"
      shift 2
      ;;
    --destinations)
      shift
      while (($#)) && [[ "$1" != --* ]]; do
        destinations+=("$1")
        shift
      done
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      usage
      fail "Unknown argument: $1"
      ;;
  esac
done

[[ $EUID -eq 0 ]] || fail "Run this script as root."
[[ "$destination_count" =~ ^[0-9]+$ ]] || fail "--destination-count must be a positive integer."
(( destination_count >= 2 )) || fail "At least two targets are required for target-pool load distribution."
(( ${#destinations[@]} == destination_count )) || fail "--destination-count ($destination_count) does not match the number of --destinations (${#destinations[@]})."
command -v rsyslogd >/dev/null || fail "rsyslog is not installed."
command -v systemctl >/dev/null || fail "systemd is required."
command -v python3 >/dev/null || fail "python3 is required to validate destination names."

if [[ -r /etc/os-release ]]; then
  . /etc/os-release
else
  fail "Cannot identify the operating system."
fi
case "${ID:-}" in
  rhel)
    [[ "${VERSION_ID:-}" == 9* ]] || fail "Only RHEL 9 is supported; detected ${VERSION_ID:-unknown}."
    ;;
  ubuntu)
    [[ "${VERSION_ID:-}" == "24.04" ]] || fail "Only Ubuntu 24.04 is supported; detected ${VERSION_ID:-unknown}."
    ;;
  *)
    fail "Supported operating systems are RHEL 9 and Ubuntu 24.04; detected ${ID:-unknown}."
    ;;
esac

declare -A seen=()
for destination in "${destinations[@]}"; do
  python3 - "$destination" <<'PY'
import ipaddress
import re
import sys

value = sys.argv[1]
try:
    ipaddress.ip_address(value)
except ValueError:
    if len(value) > 253:
        raise SystemExit("Destination is not a valid IP address or DNS name.")
    labels = value.rstrip(".").split(".")
    valid = all(
        1 <= len(label) <= 63
        and re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?", label)
        for label in labels
    )
    if not valid:
        raise SystemExit("Destination is not a valid IP address or DNS name.")
PY
  [[ -z "${seen[$destination]+x}" ]] || fail "Duplicate destination: $destination"
  seen["$destination"]=1
done

config_dir="/etc/rsyslog.d"
config_file="${config_dir}/49-ama-target-pool.conf"
state_dir="/var/lib/rsyslog-ama-target-pool"
mkdir -p "$config_dir" "$state_dir" /var/spool/rsyslog /var/log/ama-lab
chmod 0750 "$state_dir"

other_configs=()
while IFS= read -r -d '' file; do
  [[ "$file" == "$config_file" ]] || other_configs+=("$file")
done < <(find /etc/rsyslog.conf "$config_dir" -maxdepth 1 -type f -name '*.conf' -print0 2>/dev/null)

if ((${#other_configs[@]})); then
  if grep -Eiq '^[[:space:]]*input[[:space:]]*\([[:space:]]*type[[:space:]]*=[[:space:]]*"imtcp".*port[[:space:]]*=[[:space:]]*"514"' "${other_configs[@]}"; then
    echo "An active TCP/514 listener already exists; preserving it and adding only the forwarding action."
    add_listener=false
  else
    add_listener=true
  fi
else
  add_listener=true
fi

module_line=""
has_imtcp_module=false
if ((${#other_configs[@]})) && grep -Eiq '^[[:space:]]*module[[:space:]]*\([[:space:]]*load[[:space:]]*=[[:space:]]*"imtcp"' "${other_configs[@]}"; then
  has_imtcp_module=true
fi
if [[ "$has_imtcp_module" == false ]]; then
  module_line='module(load="imtcp")'
fi

target_values=""
for destination in "${destinations[@]}"; do
  escaped=${destination//\\/\\\\}
  escaped=${escaped//\"/\\\"}
  if [[ -n "$target_values" ]]; then
    target_values+=", "
  fi
  target_values+="\"${escaped}\""
done

candidate=$(mktemp "${config_dir}/.49-ama-target-pool.XXXXXX")
{
  [[ -z "$module_line" ]] || printf '%s\n' "$module_line"
  if [[ "$add_listener" == true ]]; then
    printf '%s\n' 'input(type="imtcp" port="514")'
  fi
  cat <<EOF
template(name="AmaLabForwarderCapture" type="string" string="%timegenerated:::date-rfc3339% %syslogfacility-text% %msg%\\n")
if (\$syslogfacility-text == "local6" or \$syslogfacility-text == "local7") then {
    action(type="omfile" file="/var/log/ama-lab/forwarder-received.log" template="AmaLabForwarderCapture")
    action(
        type="omfwd"
        target=[${target_values}]
        port="514"
        protocol="tcp"
        template="RSYSLOG_SyslogProtocol23Format"
        action.resumeRetryCount="-1"
        queue.type="LinkedList"
        queue.filename="ama_target_pool"
        queue.size="100000"
        queue.maxDiskSpace="1g"
        queue.saveOnShutdown="on"
        queue.discardSeverity="8"
        queue.workerThreads="1"
    )
    stop
}
EOF
} >"$candidate"
chmod 0640 "$candidate"

backup=""
if [[ -e "$config_file" ]]; then
  backup="${state_dir}/49-ama-target-pool.$(date -u +%Y%m%dT%H%M%SZ).bak"
  cp -p "$config_file" "$backup"
fi

restore_config() {
  if [[ -n "$backup" ]]; then
    cp -p "$backup" "$config_file"
  else
    rm -f "$config_file"
  fi
  rm -f "$candidate"
}

if ! rsyslogd -N1; then
  rm -f "$candidate"
  fail "Existing rsyslog configuration is invalid; no changes were activated."
fi

mv -f "$candidate" "$config_file"
if ! rsyslogd -N1; then
  restore_config
  fail "Generated rsyslog configuration failed validation; the previous file was restored."
fi

if ! systemctl restart rsyslog; then
  restore_config
  rsyslogd -N1 || fail "Rollback restored the prior file, but rsyslog validation still fails."
  systemctl restart rsyslog || fail "Rollback restored the prior file, but rsyslog restart failed."
  fail "Rsyslog activation failed; the prior configuration was restored."
fi

rm -f "$candidate"
echo "Configured one TCP omfwd target pool for ${destination_count} destinations."
echo "Rsyslog uses a one-worker linked-list queue with a 1 GiB disk limit and indefinite retry."
