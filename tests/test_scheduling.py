import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MANIFESTS = tuple((ROOT / "deploy").rglob("*.yaml"))
GENERAL_WORKLOADS = (
    ROOT / "deploy/base/server-deployment.yaml",
    ROOT / "deploy/base/gateway-deployment.yaml",
)
DATABASE_WORKLOADS = (
    ROOT / "deploy/overlays/kind/redis-deployment.yaml",
    ROOT / "deploy/overlays/kind/postgres-statefulset.yaml",
)
LAB_WORKLOADS = (
    *GENERAL_WORKLOADS,
    *DATABASE_WORKLOADS,
)


class WorkerSchedulingTests(unittest.TestCase):
    def test_general_workloads_have_no_hard_node_placement(self) -> None:
        forbidden = (
            r"^\s*nodeName\s*:",
            r"^\s*nodeSelector\s*:",
            r"^\s*requiredDuringSchedulingIgnoredDuringExecution\s*:",
            r"^\s*whenUnsatisfiable\s*:\s*DoNotSchedule\s*$",
        )
        for manifest in GENERAL_WORKLOADS:
            content = manifest.read_text(encoding="utf-8")
            for setting in forbidden:
                with self.subTest(manifest=manifest.relative_to(ROOT), setting=setting):
                    self.assertNotRegex(content, re.compile(setting, re.MULTILINE))

    def test_kind_databases_require_the_database_worker(self) -> None:
        for manifest in DATABASE_WORKLOADS:
            content = manifest.read_text(encoding="utf-8")
            with self.subTest(manifest=manifest.relative_to(ROOT)):
                self.assertIn("requiredDuringSchedulingIgnoredDuringExecution", content)
                self.assertIn("tcp-lab.io/database", content)
                self.assertIn("effect: NoSchedule", content)

    def test_kind_database_node_registers_with_taint(self) -> None:
        content = (ROOT / "kind/cluster.yaml").read_text(encoding="utf-8")
        self.assertIn(
            'register-with-taints: "tcp-lab.io/database=true:NoSchedule"',
            content,
        )

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
