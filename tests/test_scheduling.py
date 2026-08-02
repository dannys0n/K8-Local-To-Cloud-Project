import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MANIFESTS = tuple((ROOT / "deploy").rglob("*.yaml"))
LAB_WORKLOADS = (
    ROOT / "deploy/base/server-deployment.yaml",
    ROOT / "deploy/base/gateway-deployment.yaml",
    ROOT / "deploy/overlays/kind/redis-deployment.yaml",
    ROOT / "deploy/overlays/kind/postgres-statefulset.yaml",
)


class WorkerSchedulingTests(unittest.TestCase):
    def test_workloads_have_no_hard_node_placement(self) -> None:
        forbidden = (
            r"^\s*nodeName\s*:",
            r"^\s*nodeSelector\s*:",
            r"^\s*requiredDuringSchedulingIgnoredDuringExecution\s*:",
            r"^\s*whenUnsatisfiable\s*:\s*DoNotSchedule\s*$",
        )
        for manifest in MANIFESTS:
            content = manifest.read_text(encoding="utf-8")
            for setting in forbidden:
                with self.subTest(manifest=manifest.relative_to(ROOT), setting=setting):
                    self.assertNotRegex(content, re.compile(setting, re.MULTILINE))

    def test_all_topology_spread_rules_are_preferences(self) -> None:
        values = []
        for manifest in MANIFESTS:
            content = manifest.read_text(encoding="utf-8")
            values.extend(re.findall(r"whenUnsatisfiable\s*:\s*(\S+)", content))

        self.assertGreater(len(values), 0, "expected at least one topology spread rule")
        self.assertEqual({"ScheduleAnyway"}, set(values))

    def test_lab_workloads_evict_promptly_after_hard_node_loss(self) -> None:
        for manifest in LAB_WORKLOADS:
            content = manifest.read_text(encoding="utf-8")
            with self.subTest(manifest=manifest.relative_to(ROOT)):
                self.assertIn("node.kubernetes.io/not-ready", content)
                self.assertIn("node.kubernetes.io/unreachable", content)
                self.assertEqual(
                    ["15", "15"],
                    re.findall(r"tolerationSeconds\s*:\s*(\d+)", content),
                )


if __name__ == "__main__":
    unittest.main()
