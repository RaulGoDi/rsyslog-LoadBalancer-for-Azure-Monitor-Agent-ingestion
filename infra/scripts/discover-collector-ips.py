#!/usr/bin/env python3
"""Resolve the fixed collector VMSS instance addresses once during forwarder bootstrap."""

import argparse
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

IMDS_TOKEN_URL = (
    "http://169.254.169.254/metadata/identity/oauth2/token"
    "?api-version=2018-02-01&resource=https%3A%2F%2Fmanagement.azure.com%2F"
)
COMPUTE_API_VERSION = "2024-07-01"


def get_json(url: str, token: str) -> dict:
    request = urllib.request.Request(
        url,
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


def get_token() -> str:
    request = urllib.request.Request(IMDS_TOKEN_URL, headers={"Metadata": "true"})
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.load(response)["access_token"]


def arm_url(config: dict) -> str:
    base = (
        "https://management.azure.com/subscriptions/"
        f"{urllib.parse.quote(config['subscription_id'], safe='')}/resourceGroups/"
        f"{urllib.parse.quote(config['resource_group_name'], safe='')}/providers/"
        "Microsoft.Compute/virtualMachineScaleSets/"
        f"{urllib.parse.quote(config['scale_set_name'], safe='')}/virtualMachines"
        f"?api-version={COMPUTE_API_VERSION}"
    )
    return base


def instance_addresses(config: dict, token: str) -> list[tuple[str, str]]:
    instances = []
    page_url = arm_url(config)
    while page_url:
        page = get_json(page_url, token)
        instances.extend(page.get("value", []))
        page_url = page.get("nextLink")

    results = []
    for instance in instances:
        instance_id = str(instance.get("instanceId", ""))
        if not instance_id:
            continue
        interfaces_url = (
            arm_url(config).split("?", 1)[0]
            + f"/{urllib.parse.quote(instance_id, safe='')}/networkInterfaces"
            + f"?api-version={COMPUTE_API_VERSION}"
        )
        interfaces_page = get_json(interfaces_url, token)
        for interface in interfaces_page.get("value", []):
            for ip_configuration in interface.get("properties", {}).get("ipConfigurations", []):
                properties = ip_configuration.get("properties", {})
                address = properties.get("privateIPAddress") or properties.get("privateIpAddress")
                if address:
                    results.append((instance_id, address))

    return sorted(set(results))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--attempts", type=int, default=60)
    parser.add_argument("--delay", type=int, default=10)
    args = parser.parse_args()

    config = json.loads(Path(args.config).read_text(encoding="utf-8"))
    expected_count = int(config["expected_count"])
    last_error = None
    for attempt in range(args.attempts):
        try:
            token = get_token()
            addresses = instance_addresses(config, token)
            if len(addresses) == expected_count:
                output = Path(args.output)
                temporary = output.with_suffix(output.suffix + ".tmp")
                temporary.write_text("".join(f"{ip}\n" for _, ip in addresses), encoding="ascii")
                temporary.replace(output)
                return 0
            last_error = RuntimeError(
                f"Expected {expected_count} collector addresses, found {len(addresses)}."
            )
        except (KeyError, OSError, ValueError, urllib.error.URLError, json.JSONDecodeError) as error:
            last_error = error

        if attempt + 1 < args.attempts:
            time.sleep(args.delay)

    print(f"ERROR: collector address discovery failed: {last_error}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
