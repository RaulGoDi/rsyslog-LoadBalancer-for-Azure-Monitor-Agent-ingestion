import argparse
import contextlib
import importlib.util
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("prepare_lab", Path(__file__).with_name("prepare_lab.py"))
prepare_lab = importlib.util.module_from_spec(spec)
spec.loader.exec_module(prepare_lab)


def sku(name="Standard_D2s_v5", family="standardDSv5Family"):
    return {
        "name": name, "family": family, "resourceType": "virtualMachines",
        "locations": ["spaincentral"], "restrictions": [],
        "locationInfo": [{"location": "spaincentral", "zones": ["1", "2", "3"]}],
        "capabilities": [
            {"name": name, "value": value} for name, value in {
                "CpuArchitectureType": "x64", "HyperVGenerations": "V1,V2",
                "PremiumIO": "True", "vCPUs": "2", "MemoryGB": "8",
            }.items()
        ],
    }


class PrepareLabTests(unittest.TestCase):
    def test_skus_filter_location_restrictions_and_architecture(self):
        blocked = sku("blocked")
        blocked["restrictions"] = [{"type": "Location"}]
        arm = sku("arm")
        arm["capabilities"][0]["value"] = "Arm64"
        self.assertEqual(
            [item["name"] for item in prepare_lab.usable_skus([blocked, arm, sku()], "spaincentral")],
            ["Standard_D2s_v5"],
        )

    def test_zones_exclude_subscription_restricted_zones(self):
        value = sku()
        value["restrictions"] = [{"type": "Zone", "restrictionInfo": {"zones": ["2"]}}]
        self.assertEqual(prepare_lab.usable_zones(value, "spaincentral"), ["1", "3"])

    def test_quota_counts_all_five_instances_and_optional_jump(self):
        required = prepare_lab.quota_requirements(sku(), sku("jump", "standardBSFamily"))
        self.assertEqual(required["cores"], 12)
        self.assertEqual(required["standarddsv5family"], 10)
        usage = [
            {"name": {"value": name}, "limit": count + 1, "currentValue": 1}
            for name, count in required.items()
        ]
        with contextlib.redirect_stdout(io.StringIO()):
            prepare_lab.check_quota(usage, required)
            usage[0]["currentValue"] = 2
            with self.assertRaisesRegex(ValueError, "Insufficient"):
                prepare_lab.check_quota(usage, required)
            with self.assertRaisesRegex(ValueError, "Cannot verify"):
                prepare_lab.check_quota([], required)

    def test_supported_os_gate_rejects_unreviewed_releases(self):
        self.assertTrue(prepare_lab.image_supported("RedHat", "RHEL", "9_7-gen2", "logging"))
        self.assertFalse(prepare_lab.image_supported("RedHat", "RHEL", "9_7", "logging"))
        self.assertFalse(prepare_lab.image_supported("RedHat", "RHEL", "10_0", "logging"))
        self.assertTrue(prepare_lab.image_supported("Canonical", "ubuntu-24_04-lts", "server", "generator"))
        self.assertFalse(prepare_lab.image_supported("Canonical", "ubuntu-26_04-lts", "server", "generator"))

    def test_image_metadata_checks(self):
        image = {"architecture": "x64", "hyperVGeneration": "V2", "osDiskImage": {"sizeInGb": 30}}
        prepare_lab.check_image(image)
        for change in (
            {"architecture": "Arm64"}, {"plan": {"name": "paid"}},
            {"osDiskImage": {"sizeInGb": 128}},
            {"imageDeprecationStatus": {"imageState": "Deprecated"}},
        ):
            with self.subTest(change=change), self.assertRaises(ValueError):
                prepare_lab.check_image({**image, **change})

    def test_image_versions_sorted_numerically_and_pinned(self):
        calls = []

        def az(*parts):
            calls.append(parts)
            if parts[:3] == ("vm", "image", "list-offers"):
                return [{"name": "RHEL"}]
            if parts[:3] == ("vm", "image", "list-skus"):
                return [{"name": "9_9-gen2"}, {"name": "9_10-gen2"}, {"name": "10_0"}]
            if parts[:3] == ("vm", "image", "list"):
                return [{"version": "9.10.9"}, {"version": "9.10.11"}]
            return {"architecture": "x64", "hyperVGeneration": "V2", "osDiskImage": {"sizeInGb": 64}}

        with patch("builtins.input", return_value=""), contextlib.redirect_stdout(io.StringIO()):
            result = prepare_lab.select_image(az, "spaincentral", "logging")
        self.assertEqual(result, ("RedHat", "RHEL", "9_10-gen2", "9.10.11"))
        self.assertIn("RedHat:RHEL:9_10-gen2:9.10.11", calls[-1])

    def test_inputs_never_overwrite_existing_file(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "inputs.json"
            prepare_lab.write_inputs(path, {"lab_vm_size": "size"})
            with self.assertRaises(FileExistsError):
                prepare_lab.write_inputs(path, {})
            self.assertEqual(json.loads(path.read_text()), {"lab_vm_size": "size"})

    def test_no_tools_installed_when_missing(self):
        args = argparse.Namespace(subscription_id="unused")
        with patch.object(prepare_lab.shutil, "which", return_value=None):
            with self.assertRaisesRegex(ValueError, "Nothing will be installed"):
                prepare_lab.prepare(args)

    def run_workflow(self, apply_answer, expected_commands):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "infra").mkdir()
            args = argparse.Namespace(
                subscription_id="00000000-0000-0000-0000-000000000000",
                location="spaincentral", var_file=None,
            )
            usage = [
                {"name": {"value": name}, "limit": 100, "currentValue": 0}
                for name in ("cores", "standardDSv5Family")
            ]
            with patch.object(prepare_lab, "__file__", str(root / "scripts" / "prepare_lab.py")), \
                    patch.object(prepare_lab.shutil, "which", side_effect=lambda name: name), \
                    patch.object(prepare_lab, "json_command", side_effect=[{}, [sku()], usage]), \
                    patch.object(prepare_lab, "choose", side_effect=[sku(), False, []]), \
                    patch.object(prepare_lab, "select_image", side_effect=[
                        ("RedHat", "RHEL", "9_7", "9.7.1"),
                        ("Canonical", "ubuntu-24_04-lts", "server", "24.04.1"),
                    ]), \
                    patch("builtins.input", side_effect=["yes", apply_answer]), \
                    patch.object(prepare_lab.subprocess, "run") as run, \
                    contextlib.redirect_stdout(io.StringIO()) as output:
                prepare_lab.prepare(args)
            commands = [call.args[0] for call in run.call_args_list]
            self.assertEqual([command[1] for command in commands], expected_commands)
            self.assertIn("-var-file=preflight.tfvars.json", commands[2])
            self.assertIn("-input=false", commands[2])
            if expected_commands[-1] == "apply":
                self.assertEqual(commands[-1], ["terraform", "apply", "-input=false", "lab.tfplan"])
            else:
                self.assertIn(f'terraform -chdir="{root / "infra"}" apply "lab.tfplan"', output.getvalue())
                self.assertIn("Not applied", output.getvalue())
            inputs = json.loads((root / "infra" / "preflight.tfvars.json").read_text())
            self.assertEqual(inputs["rhel_image_version"], "9.7.1")
            self.assertFalse(inputs["enable_jump_host"])

    def test_declined_apply_prints_exact_later_command(self):
        self.run_workflow("no", ["init", "validate", "plan"])

    def test_confirmed_apply_uses_saved_plan(self):
        self.run_workflow("yes", ["init", "validate", "plan", "apply"])

    def test_apply_defaults_to_no(self):
        self.run_workflow("", ["init", "validate", "plan"])

    def test_confirm_reprompts_on_invalid_response(self):
        with patch("builtins.input", side_effect=["maybe", "yes"]), \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertTrue(prepare_lab.confirm("Continue?"))


if __name__ == "__main__":
    unittest.main()
