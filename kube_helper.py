import subprocess
import json
from kubernetes import client, config

try:
    config.load_incluster_config()
except:
    config.load_kube_config()

apps_v1 = client.AppsV1Api()
core_v1 = client.CoreV1Api()

NAMESPACE = "default"


# ---------- Deployment Discovery ----------
def get_target_deployments():
    deps = apps_v1.list_namespaced_deployment(NAMESPACE)
    result = []

    for d in deps.items:
        labels = d.metadata.labels or {}
        if labels.get("rl-autoscale") == "true":
            result.append(d.metadata.name)

    return result


# ---------- Core Kubernetes ----------
def get_current_replicas(dep_name):
    dep = apps_v1.read_namespaced_deployment(dep_name, NAMESPACE)
    return dep.spec.replicas


def scale_deployment(dep_name, new_replicas):
    body = {"spec": {"replicas": new_replicas}}
    apps_v1.patch_namespaced_deployment_scale(
        name=dep_name,
        namespace=NAMESPACE,
        body=body,
    )
    print(f"🚀 {dep_name} → {new_replicas} replicas")


def get_resource_requests(dep_name):
    dep = apps_v1.read_namespaced_deployment(dep_name, NAMESPACE)
    containers = dep.spec.template.spec.containers

    cpu_request = 0.0
    mem_request = 0.0

    for c in containers:
        res = c.resources.requests or {}
        if "cpu" in res:
            cpu_request += parse_cpu(res["cpu"])
        if "memory" in res:
            mem_request += parse_memory(res["memory"])

    return cpu_request, mem_request


# ---------- Metrics ----------
def get_all_pod_metrics():
    cmd = [
        "kubectl",
        "get",
        "--raw",
        f"/apis/metrics.k8s.io/v1beta1/namespaces/{NAMESPACE}/pods"
    ]

    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

    if result.returncode != 0:
        print("Error fetching metrics:", result.stderr)
        return []

    data = json.loads(result.stdout)
    return data.get("items", [])


def get_pod_metrics_for_dep(dep_name, all_pods):
    dep = apps_v1.read_namespaced_deployment(dep_name, NAMESPACE)
    selector = dep.spec.selector.match_labels

    matched = []

    for pod in all_pods:
        labels = pod["metadata"].get("labels", {})

        if all(labels.get(k) == v for k, v in selector.items()):
            matched.append(pod)

    return matched


# ---------- Parsers ----------
def parse_cpu(cpu_str):
    cpu_str = str(cpu_str)

    if cpu_str.endswith("n"):
        return int(cpu_str[:-1]) / 1_000_000

    if cpu_str.endswith("u"):
        return int(cpu_str[:-1]) / 1000

    if cpu_str.endswith("m"):
        return float(cpu_str[:-1])

    return float(cpu_str) * 1000


def parse_memory(mem_str):
    if mem_str.endswith("Ki"):
        return int(mem_str[:-2]) / 1024
    if mem_str.endswith("Mi"):
        return float(mem_str[:-2])
    if mem_str.endswith("Gi"):
        return float(mem_str[:-2]) * 1024
    return float(mem_str)
