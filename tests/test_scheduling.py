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
CAPACITY_RESERVE = ROOT / "deploy/base/capacity-reserve-deployment.yaml"
PRIORITY_CLASSES = ROOT / "deploy/base/priority-classes.yaml"


class WorkerSchedulingTests(unittest.TestCase):
    def test_general_workloads_have_no_hard_node_placement(self) -> None:
        forbidden = (
            r"^\s*nodeName\s*:",
            r"^\s*nodeSelector\s*:",
            r"^\s*requiredDuringSchedulingIgnoredDuringExecution\s*:",
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

    def test_kind_node_roles_and_fast_monitoring(self) -> None:
        content = (ROOT / "kind/cluster.yaml").read_text(encoding="utf-8")
        self.assertIn(
            'register-with-taints: "tcp-lab.io/database=true:NoSchedule"',
            content,
        )
        first_worker = content.index("  - role: worker")
        second_worker = content.index("  - role: worker", first_worker + 1)
        database_taint = content.index(
            'register-with-taints: "tcp-lab.io/database=true:NoSchedule"'
        )
        self.assertLess(first_worker, database_taint)
        self.assertLess(database_taint, second_worker)
        self.assertIn('node-monitor-grace-period: "5s"', content)
        self.assertIn('node-monitor-period: "1s"', content)
        self.assertIn('node-eviction-rate: "10"', content)
        self.assertIn('secondary-node-eviction-rate: "10"', content)
        self.assertIn('large-cluster-size-threshold: "1"', content)
        self.assertIn("nodeStatusUpdateFrequency: 1s", content)

    def test_all_topology_spread_rules_protect_warm_capacity(self) -> None:
        values = []
        for manifest in MANIFESTS:
            content = manifest.read_text(encoding="utf-8")
            values.extend(re.findall(r"whenUnsatisfiable\s*:\s*(\S+)", content))

        self.assertGreater(len(values), 0, "expected at least one topology spread rule")
        self.assertEqual({"DoNotSchedule"}, set(values))

        for manifest in GENERAL_WORKLOADS:
            content = manifest.read_text(encoding="utf-8")
            self.assertIn("nodeTaintsPolicy: Honor", content)
            self.assertIn("pod-template-hash", content)

    def test_lab_workloads_evict_promptly_after_hard_node_loss(self) -> None:
        for manifest in GENERAL_WORKLOADS:
            content = manifest.read_text(encoding="utf-8")
            with self.subTest(manifest=manifest.relative_to(ROOT)):
                self.assertIn("node.kubernetes.io/not-ready", content)
                self.assertIn("node.kubernetes.io/unreachable", content)
                self.assertEqual(
                    ["0", "0"],
                    re.findall(r"tolerationSeconds\s*:\s*(\d+)", content),
                )
                self.assertIn("startupProbe:", content)
                self.assertRegex(
                    content,
                    re.compile(
                        r"startupProbe:.*?periodSeconds:\s*1.*?failureThreshold:\s*90",
                        re.DOTALL,
                    ),
                )

        for manifest in DATABASE_WORKLOADS:
            content = manifest.read_text(encoding="utf-8")
            with self.subTest(manifest=manifest.relative_to(ROOT)):
                self.assertEqual(
                    ["15", "15"],
                    re.findall(r"tolerationSeconds\s*:\s*(\d+)", content),
                )

    def test_critical_workloads_preempt_server_sized_reservations(self) -> None:
        priorities = PRIORITY_CLASSES.read_text(encoding="utf-8")
        reserve = CAPACITY_RESERVE.read_text(encoding="utf-8")
        server = GENERAL_WORKLOADS[0].read_text(encoding="utf-8")

        self.assertIn("name: tcp-lab-critical", priorities)
        self.assertIn("name: tcp-lab-reserve", priorities)
        self.assertIn("priorityClassName: tcp-lab-reserve", reserve)
        self.assertIn("replicas: 10", reserve)
        self.assertIn("cpu: 25m", reserve)
        self.assertIn("memory: 32Mi", reserve)
        self.assertIn("priorityClassName: tcp-lab-critical", server)
        for workload in GENERAL_WORKLOADS:
            self.assertIn(
                "priorityClassName: tcp-lab-critical",
                workload.read_text(encoding="utf-8"),
            )


if __name__ == "__main__":
    unittest.main()
