import subprocess
import json
from kubernetes import client, config

# ---------- Config ----------
try:
    config.load_incluster_config()
    print("Using in-cluster config")
except Exception as e:
    print("Falling back:", e)
    config.load_kube_config()

apps_v1 = client.AppsV1Api()


# ---------- Deployment Discovery ----------
def get_target_deployments():
    deps = apps_v1.list_deployment_for_all_namespaces()
    result = []

    for d in deps.items:
        labels = d.spec.template.metadata.labels or {}

        if labels.get("rl-autoscale") == "true":
            result.append({
                "name": d.metadata.name,
                "namespace": d.metadata.namespace
            })

    return result


# ---------- Core Kubernetes ----------
def get_current_replicas(name, namespace):
    dep = apps_v1.read_namespaced_deployment(name, namespace)
    return dep.spec.replicas


def scale_deployment(name, namespace, new_replicas):
    body = {"spec": {"replicas": new_replicas}}
    apps_v1.patch_namespaced_deployment_scale(
        name=name,
        namespace=namespace,
        body=body,
    )
    print(f"🚀 {namespace}/{name} → {new_replicas} replicas")


def get_resource_requests(name, namespace):
    dep = apps_v1.read_namespaced_deployment(name, namespace)
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
def get_all_pod_metrics(namespace):
    cmd = [
        "kubectl",
        "get",
        "--raw",
        f"/apis/metrics.k8s.io/v1beta1/namespaces/{namespace}/pods"
    ]

    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

    if result.returncode != 0:
        print("Error fetching metrics:", result.stderr)
        return []

    data = json.loads(result.stdout)
    return data.get("items", [])


def get_pod_metrics_for_dep(name, namespace, all_pods):
    dep = apps_v1.read_namespaced_deployment(name, namespace)
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
