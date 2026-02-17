import json
import logging
import os
import time
from typing import List

from kubernetes import client, config
from kubernetes.client.rest import ApiException

from settings import NAMESPACE

logger = logging.getLogger(__name__)

_k8s_api = None


def _load_k8s_config() -> None:
    try:
        config.load_incluster_config()
    except Exception:  # noqa: BLE001
        config.load_kube_config()


def get_k8s_api():
    global _k8s_api
    if _k8s_api is None:
        _load_k8s_config()
        _k8s_api = client.AppsV1Api()
    return _k8s_api


def get_core_v1_api():
    if not hasattr(get_core_v1_api, "_api"):
        _load_k8s_config()
        get_core_v1_api._api = client.CoreV1Api()
    return get_core_v1_api._api


def wait_for_game_server_ready(session_id: str, timeout_seconds: float = 45.0) -> bool:
    core_api = get_core_v1_api()
    label_selector = f"app=game-server,session_id={session_id}"
    deadline = time.time() + timeout_seconds

    while time.time() < deadline:
        try:
            pods = core_api.list_namespaced_pod(
                namespace=NAMESPACE,
                label_selector=label_selector,
            )
            for pod in pods.items:
                status = pod.status
                if status is None or status.phase != "Running":
                    continue
                if any(cs.ready for cs in (status.container_statuses or [])):
                    return True
        except Exception as e:  # noqa: BLE001
            logger.warning(f"Error while waiting for game server readiness ({session_id}): {e}")
        time.sleep(0.5)
    return False


def create_game_server_pod(session_id: str, players: List[str]) -> tuple[str, str, int]:
    k8s_apps_api = get_k8s_api()
    core_api = get_core_v1_api()
    game_server_image = os.getenv("GAME_SERVER_IMAGE", "game-server:local")

    pod_name = f"game-server-{session_id[:8]}"
    logger.info(
        f"Creating game server pod {pod_name} in namespace {NAMESPACE} using image {game_server_image}"
    )

    deployment = client.V1Deployment(
        metadata=client.V1ObjectMeta(
            name=pod_name,
            namespace=NAMESPACE,
            labels={"app": "game-server", "session_id": session_id},
        ),
        spec=client.V1DeploymentSpec(
            replicas=1,
            selector=client.V1LabelSelector(
                match_labels={"app": "game-server", "session_id": session_id}
            ),
            template=client.V1PodTemplateSpec(
                metadata=client.V1ObjectMeta(
                    labels={"app": "game-server", "session_id": session_id}
                ),
                spec=client.V1PodSpec(
                    containers=[
                        client.V1Container(
                            name="game-server",
                            image=game_server_image,
                            image_pull_policy="IfNotPresent",
                            ports=[client.V1ContainerPort(container_port=8080)],
                            env=[
                                client.V1EnvVar(name="SESSION_ID", value=session_id),
                                client.V1EnvVar(name="PLAYERS", value=json.dumps(players)),
                                client.V1EnvVar(name="PORT", value="8080"),
                            ],
                        )
                    ],
                    restart_policy="Always",
                ),
            ),
        ),
    )

    try:
        k8s_apps_api.create_namespaced_deployment(namespace=NAMESPACE, body=deployment)
        logger.info(f"Successfully created deployment {pod_name}")
    except ApiException as e:
        logger.error(f"Failed to create deployment: status={e.status}, reason={e.reason}, body={e.body}")
        if e.status != 409:
            raise

    svc = client.V1Service(
        metadata=client.V1ObjectMeta(
            name=pod_name,
            namespace=NAMESPACE,
            labels={"app": "game-server", "session_id": session_id},
        ),
        spec=client.V1ServiceSpec(
            type="NodePort",
            selector={"app": "game-server", "session_id": session_id},
            ports=[client.V1ServicePort(port=8080, target_port=8080, protocol="TCP")],
        ),
    )
    try:
        core_api.create_namespaced_service(namespace=NAMESPACE, body=svc)
        logger.info(f"Created Service {pod_name} (NodePort)")
    except ApiException as e:
        if e.status != 409:
            logger.warning(f"Failed to create Service for {pod_name}: {e}")
            return pod_name, "", 0

    time.sleep(0.5)
    try:
        created = core_api.read_namespaced_service(name=pod_name, namespace=NAMESPACE)
        node_port = created.spec.ports[0].node_port if created.spec.ports else 0
    except Exception:  # noqa: BLE001
        node_port = 0

    connect_host = os.getenv("GAME_SERVER_CONNECT_HOST", "")
    if not connect_host:
        try:
            nodes = core_api.list_node()
            for node in nodes.items:
                for addr in node.status.addresses or []:
                    if addr.type in ("ExternalIP", "InternalIP"):
                        connect_host = addr.address
                        break
                if connect_host:
                    break
        except Exception as e:  # noqa: BLE001
            logger.warning(f"Failed to auto-detect node IP for game server connect_host: {e}")
    if not connect_host:
        connect_host = "localhost"
        logger.warning(
            "Falling back to localhost for game-server connect_host; this may be unreachable for NodePort clients"
        )

    if not wait_for_game_server_ready(session_id, timeout_seconds=45.0):
        logger.warning(f"Game server {pod_name} not ready within timeout; clients may need to retry connect")

    return pod_name, connect_host, node_port or 0


def delete_game_server_pod(session_id: str) -> None:
    k8s_apps_api = get_k8s_api()
    core_api = get_core_v1_api()
    pod_name = f"game-server-{session_id[:8]}"

    try:
        core_api.delete_namespaced_service(name=pod_name, namespace=NAMESPACE)
        logger.info(f"Deleted Service {pod_name}")
    except ApiException as e:
        if e.status != 404:
            logger.warning(f"Failed to delete Service {pod_name}: {e}")

    max_retries = 3
    for attempt in range(max_retries):
        try:
            k8s_apps_api.delete_namespaced_deployment(
                name=pod_name,
                namespace=NAMESPACE,
                body=client.V1DeleteOptions(propagation_policy="Foreground"),
            )
            logger.info(f"Successfully deleted game server pod {pod_name}")
            return
        except ApiException as e:
            if e.status == 404:
                logger.info(f"Game server pod {pod_name} already deleted")
                return
            if attempt < max_retries - 1:
                wait_time = 2 ** attempt
                logger.warning(f"Failed to delete {pod_name} (attempt {attempt + 1}/{max_retries}): {e}, retrying in {wait_time}s")
                time.sleep(wait_time)
            else:
                logger.error(f"Failed to delete {pod_name} after {max_retries} attempts: {e}")
                raise
