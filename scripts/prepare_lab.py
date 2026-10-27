#!/usr/bin/env python3
"""Discover Azure lab choices, plan the deployment, and optionally apply after confirmation."""

from __future__ import annotations

import argparse
import ipaddress
import json
import re
import shutil
import subprocess
import sys
import uuid
from collections import Counter
from pathlib import Path


def json_command(command: list[str]):
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode:
        raise RuntimeError(f"Command failed: {' '.join(command)}\n{result.stderr.strip()}")
    return json.loads(result.stdout)


def choose(title: str, items: list, label=str):
    if not items:
        raise ValueError(f"No available choices: {title}")
    print(f"\n{title}")
    for index, item in enumerate(items, 1):
        print(f"  {index}. {label(item)}")
    while True:
        answer = input("Select number [1]: ").strip() or "1"
        if answer.isdigit() and 1 <= int(answer) <= len(items):
            return items[int(answer) - 1]
        print("Enter one of the listed numbers.")


def confirm(message: str) -> bool:
    while True:
        answer = input(f"{message} [yes/no, default no]: ").strip().lower()
        if answer in ("y", "yes"):
            return True
        if answer in ("", "n", "no"):
            return False
        print("Enter yes or no.")


def capabilities(sku: dict) -> dict:
    return {item["name"]: item["value"] for item in sku.get("capabilities", [])}


def usable_skus(skus: list[dict], region: str) -> list[dict]:
    result = []
    for sku in skus:
        if sku.get("resourceType") != "virtualMachines":
            continue
        if any(item.get("type") == "Location" for item in sku.get("restrictions", [])):
            continue
        caps = capabilities(sku)
        if (
            caps.get("CpuArchitectureType") != "x64"
            or caps.get("PremiumIO", "").lower() not in ("true", "false")
            or "V2" not in [value.strip() for value in caps.get("HyperVGenerations", "").split(",")]
            or caps.get("VMDeploymentTypes", "IaaS").find("IaaS") == -1
            or float(caps.get("vCPUs", "0")) < 2
            or float(caps.get("MemoryGB", "0")) < 4
        ):
            continue
        if region.lower() not in [value.lower() for value in sku.get("locations", [])]:
            continue
        result.append(sku)
    return sorted(result, key=lambda sku: (
        sku["name"] != "Standard_D2s_v5",
        float(capabilities(sku)["vCPUs"]), float(capabilities(sku)["MemoryGB"]), sku["name"],
    ))


def usable_zones(sku: dict, region: str) -> list[str]:
    zones = {
        zone for info in sku.get("locationInfo", [])
        if info.get("location", "").lower() == region.lower()
        for zone in info.get("zones", [])
    }
    for restriction in sku.get("restrictions", []):
        if restriction.get("type") == "Zone":
            blocked = restriction.get("restrictionInfo", {}).get("zones", [])
            if not blocked:
                return []
            zones.difference_update(blocked)
    return sorted(zones)


def quota_requirements(lab_sku: dict, jump_sku: dict | None) -> Counter:
    needed: Counter = Counter()
    for sku, count in ((lab_sku, 5), (jump_sku, 1)):
        if sku is not None:
            cpu = int(capabilities(sku)["vCPUs"])
            needed[sku["family"].lower()] += cpu * count
            needed["cores"] += cpu * count
    return needed


def check_quota(usage: list[dict], required: Counter) -> None:
    by_name = {item["name"]["value"].lower(): item for item in usage}
    for name, needed in required.items():
        if name not in by_name:
            raise ValueError(f"Cannot verify compute quota {name}; check quota in Azure before continuing.")
        item = by_name[name]
        available = int(item["limit"]) - int(item["currentValue"])
        print(f"Quota {name}: {available} remaining, {needed} additional vCPUs required")
        if available < needed:
            raise ValueError(f"Insufficient quota for {name}. Choose a smaller size or request more quota.")


def numeric_version(value: str) -> tuple:
    return tuple(int(part) for part in re.findall(r"\d+", value))


def image_supported(publisher: str, offer: str, sku: str, role: str) -> bool:
    if role == "logging":
        return publisher == "RedHat" and offer == "RHEL" and bool(re.fullmatch(r"9[_\.]\d+-gen2", sku))
    return (
        publisher == "Canonical" and "24_04" in offer and "lts" in offer.lower()
        and sku == "server"
    )


def check_image(image: dict) -> None:
    if image.get("architecture", "").lower() != "x64" or image.get("hyperVGeneration") != "V2":
        raise ValueError("Selected image must explicitly support x64 and Hyper-V generation V2.")
    if image.get("plan"):
        raise ValueError("Selected image requires a Marketplace purchase plan not wired into this lab.")
    properties = image.get("imageDeprecationStatus") or {}
    if properties.get("imageState", "Active").lower() != "active":
        raise ValueError("Selected image is deprecated or scheduled for deprecation.")
    disk_size = image.get("osDiskImage", {}).get("sizeInGb")
    if disk_size is None or disk_size > 64:
        raise ValueError("Cannot fit the selected image in the configured 64-GiB lab OS disk.")


def select_image(az, region: str, role: str) -> tuple[str, str, str, str]:
    publisher = "RedHat" if role == "logging" else "Canonical"
    offers = az("vm", "image", "list-offers", "--location", region, "--publisher", publisher)
    candidates = []
    for offer_item in offers:
        offer = offer_item["name"]
        if role == "logging" and offer != "RHEL":
            continue
        if role != "logging" and not ("24_04" in offer and "lts" in offer.lower()):
            continue
        skus = az(
            "vm", "image", "list-skus", "--location", region,
            "--publisher", publisher, "--offer", offer,
        )
        for item in skus:
            if image_supported(publisher, offer, item["name"], role):
                candidates.append((publisher, offer, item["name"]))
    candidates.sort(key=lambda item: numeric_version(item[2].removesuffix("-gen2")), reverse=True)
    if candidates:
        selection = choose(
            f"{role} image (newest supported release first)", candidates,
            lambda item: ":".join(item),
        )
    else:
        print(
            f"No bootstrap-compatible {role} image found. Alternate catalog entries can be "
            "inspected below, but unsupported OS families/releases require bootstrap review before planning."
        )
        publishers = az("vm", "image", "list-publishers", "--location", region)
        publisher = choose("Inspect alternative publisher", publishers, lambda item: item["name"])["name"]
        offers = az("vm", "image", "list-offers", "--location", region, "--publisher", publisher)
        offer = choose("Inspect alternative offer", offers, lambda item: item["name"])["name"]
        skus = az(
            "vm", "image", "list-skus", "--location", region,
            "--publisher", publisher, "--offer", offer,
        )
        sku = choose("Inspect alternative OS SKU", skus, lambda item: item["name"])["name"]
        if not image_supported(publisher, offer, sku, role):
            raise ValueError(
                f"{publisher}:{offer}:{sku} is not supported by the current {role} bootstrap. "
                "Update and test the bootstrap before deploying this OS. No plan will be run."
            )
        selection = publisher, offer, sku
    images = az(
        "vm", "image", "list", "--all", "--location", region,
        "--publisher", selection[0], "--offer", selection[1], "--sku", selection[2],
    )
    if not images:
        raise ValueError("No image versions available for the selected OS SKU.")
    versions = sorted(images, key=lambda image: numeric_version(image["version"]), reverse=True)
    chosen = choose("Pin image version (newest first)", versions, lambda image: image["version"])
    urn = ":".join((*selection, chosen["version"]))
    check_image(az("vm", "image", "show", "--location", region, "--urn", urn))
    return (*selection, chosen["version"])


def write_inputs(path: Path, inputs: dict) -> None:
    # Exclusive creation prevents accidental replacement of an existing deployment's choices.
    with path.open("x", encoding="utf-8") as output:
        json.dump(inputs, output, indent=2)
        output.write("\n")


def prepare(args: argparse.Namespace) -> None:
    az_path = shutil.which("az")
    terraform = shutil.which("terraform")
    if az_path is None or terraform is None:
        raise ValueError("Azure CLI and Terraform must already be installed. Nothing will be installed automatically.")
    subscription = str(uuid.UUID(args.subscription_id))
    infra = Path(__file__).resolve().parents[1] / "infra"
    input_path = infra / "preflight.tfvars.json"
    plan_path = infra / "lab.tfplan"
    if input_path.exists() or plan_path.exists():
        raise ValueError(
            "preflight.tfvars.json or lab.tfplan already exists. Preserve existing deployment inputs "
            "and review/remove these files explicitly before starting another selection."
        )

    def az(*parts):
        return json_command([
            az_path, *parts, "--subscription", subscription, "--output", "json", "--only-show-errors",
        ])

    az("account", "show")
    skus = usable_skus(
        az("vm", "list-skus", "--location", args.location, "--resource-type", "virtualMachines", "--all"),
        args.location,
    )
    def label(sku):
        caps = capabilities(sku)
        return f"{sku['name']} ({caps['vCPUs']} vCPU, {caps['MemoryGB']} GiB); verify price separately"

    lab_sku = choose("Size for five lab VMs (2+ vCPUs, 4+ GiB, x64, Gen2)", skus, label)
    jump_sku = None
    jump_enabled = choose("Create optional jump host?", [False, True], lambda value: "Yes" if value else "No")
    admin_cidr = None
    if jump_enabled:
        jump_sku = choose("Jump host size", skus, label)
        admin_cidr = input("Your administrator public IPv4 CIDR (for example 203.0.113.10/32): ").strip()
        network = ipaddress.ip_network(admin_cidr, strict=False)
        if network.version != 4 or network.prefixlen == 0:
            raise ValueError("An IPv4 administrator CIDR narrower than /0 is required.")
    zones = usable_zones(lab_sku, args.location)
    selected_zones = choose(
        "VM scale-set zone placement",
        [zones, []] if len(zones) >= 2 else [[]],
        lambda value: ", ".join(value) if value else "No explicit zones",
    )
    # Built-in compute usage avoids installing the optional Azure CLI quota extension.
    check_quota(
        az("vm", "list-usage", "--location", args.location),
        quota_requirements(lab_sku, jump_sku),
    )
    logging_image = select_image(az, args.location, "logging")
    generator_image = select_image(az, args.location, "generator")
    inputs = {
        "subscription_id": subscription, "location": args.location,
        "lab_vm_size": lab_sku["name"], "availability_zones": selected_zones,
        "enable_jump_host": jump_enabled,
    }
    if jump_sku:
        inputs.update({"jump_vm_size": jump_sku["name"], "admin_cidr": admin_cidr})
        if generator_image:
            print("Jump host OS disk will be 64 GiB to match the verified generator image.")
    for prefix, image in (("rhel", logging_image), ("ubuntu", generator_image)):
        for suffix, value in zip(("publisher", "offer", "sku", "version"), image):
            inputs[f"{prefix}_image_{suffix}"] = value
    print(json.dumps(inputs, indent=2))
    print(
        "Checks are catalog/quota checks, not guarantees of physical capacity, AMA support, "
        "rsyslog target-pool support, OS lifecycle, or bootstrap success. Review prices separately."
    )
    if not confirm("Save selections and run Terraform init/validate/plan?"):
        print("Cancelled; no inputs written or Terraform commands run.")
        return
    write_inputs(input_path, inputs)
    commands = [
        [terraform, "init", "-input=false"],
        [terraform, "validate"],
        [
            terraform, "plan", "-input=false",
            *([f"-var-file={args.var_file.resolve()}"] if args.var_file else []),
            f"-var-file={input_path.name}", f"-out={plan_path.name}",
        ],
    ]
    for command in commands:
        subprocess.run(command, cwd=infra, check=True)
    apply_command = f'terraform -chdir="{infra}" apply "{plan_path.name}"'
    print(f"Saved plan: {plan_path}")
    print("Review the plan shown above. Applying creates billable Azure resources.")
    if confirm("Apply this saved plan now?"):
        subprocess.run(
            [terraform, "apply", "-input=false", plan_path.name], cwd=infra, check=True
        )
        print("Terraform apply completed. VM bootstrap and the distribution tests still need verification.")
        print(f'Retrieve the lab password privately: terraform -chdir="{infra}" output -raw admin_password')
        print("LAB ONLY: Change the password on every VM if keeping the VMs beyond testing.")
    else:
        print("Not applied. To apply the saved plan later, run:")
        print(apply_command)
    print(f"For later plan/destroy commands, keep using -var-file={input_path.name}.")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--subscription-id", required=True)
    parser.add_argument("--location", default="spaincentral")
    parser.add_argument("--var-file", type=Path, help="Optional base inputs; discovered choices override them.")
    args = parser.parse_args()
    try:
        prepare(args)
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError, EOFError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
