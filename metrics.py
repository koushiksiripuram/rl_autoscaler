import time
import requests
from rl_agent import RLAgent
from kube_helper import *

PROMETHEUS_URL = "http://prometheus.monitoring.svc.cluster.local:9090"
INTERVAL = 15

TARGET_CPU_UTIL = 60
TARGET_MEM_UTIL = 70
RPS_PER_POD = 20
COOLDOWN = 60

agents = {}
last_scale_time = {}

def compute_desired_replicas(cpu_util, mem_util, http_rps, current_replicas,
                             target_cpu, target_mem, rps_per_pod,
                             min_replicas, max_replicas):

    cpu_based = int((cpu_util / target_cpu) * current_replicas) if cpu_util else current_replicas
    mem_based = int((mem_util / target_mem) * current_replicas) if mem_util else current_replicas
    rps_based = int(http_rps / rps_per_pod) + 1 if http_rps else current_replicas

    desired = max(cpu_based, mem_based, rps_based)

    desired = max(min_replicas, min(max_replicas, desired))

    return desired, cpu_based, mem_based, rps_based
# ---------- Prometheus ----------
def query_prometheus(promql):
    try:
        resp = requests.get(
            f"{PROMETHEUS_URL}/api/v1/query",
            params={"query": promql},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        print("Prometheus error:", e)
        return 0.0

    if data["status"] != "success" or not data["data"]["result"]:
        return 0.0

    return float(data["data"]["result"][0]["value"][1])


def get_http_rps(dep_name):
    return query_prometheus(
        f'rate(nginx_http_requests_total{{app="{dep_name}"}}[1m])'
    )


print("\n🚦 Multi-Deployment RL Autoscaler Running\n")

while True:
    deployments = get_target_deployments()

    if not deployments:
        print("No rl-autoscale deployments")
        time.sleep(INTERVAL)
        continue

    for dep in deployments:
        name = dep["name"]
        namespace = dep["namespace"]
        key = (namespace, name)

        print("\n" + "=" * 60)
        print(f"🔍 Processing: {namespace}/{name}")

        now = time.time()

        if key not in last_scale_time:
            last_scale_time[key] = 0

        all_pods = get_all_pod_metrics(namespace)
        pods = get_pod_metrics_for_dep(name, namespace, all_pods)

        if not pods:
            print("No pods found")
            continue

        total_cpu = 0
        total_mem = 0

        for pod in pods:
            pod_cpu = sum(parse_cpu(c["usage"]["cpu"]) for c in pod["containers"])
            pod_mem = sum(parse_memory(c["usage"]["memory"]) for c in pod["containers"])

            total_cpu += pod_cpu
            total_mem += pod_mem

        pod_count = len(pods)
        avg_cpu = total_cpu / pod_count
        avg_mem = total_mem / pod_count

        cpu_request, mem_request = get_resource_requests(name, namespace)

        if key not in agents:
            agents[key] = RLAgent(
                state_dim=5,
                action_dim=2,
                min_replicas=1,
                max_replicas=10,
                cpu_request=cpu_request,
                mem_request=mem_request,
                cooldown_seconds=COOLDOWN,
                rps_per_pod=RPS_PER_POD
            )

        agent = agents[key]

        cpu_util = (avg_cpu / cpu_request) * 100 if cpu_request else 0
        mem_util = (avg_mem / mem_request) * 100 if mem_request else 0
        http_rps = get_http_rps(name)

        print(f"CPU: {cpu_util:.1f}% | MEM: {mem_util:.1f}% | RPS: {http_rps:.2f}")

        current_replicas = get_current_replicas(name, namespace)

        desired, cpu_d, mem_d, rps_d = compute_desired_replicas(
            cpu_util, mem_util, http_rps, current_replicas,
            TARGET_CPU_UTIL, TARGET_MEM_UTIL,
            RPS_PER_POD, 1, 10
        )

        print(f"Desired → CPU:{cpu_d} MEM:{mem_d} RPS:{rps_d} FINAL:{desired}")

        if now - last_scale_time[key] < COOLDOWN:
            print("⏳ Cooldown active")
            continue

        # SCALE UP
        if desired > current_replicas:
            print("📈 Scaling UP")
            scale_deployment(name, namespace, desired)
            last_scale_time[key] = now

        # SCALE DOWN (RL)
        elif desired < current_replicas:
            state = agent.build_state(
                current_replicas,
                avg_cpu,
                avg_mem,
                http_rps,
                now - last_scale_time[key]
            )

            action = agent.act(state, training=True)

            if action == 1:
                new_replicas = max(current_replicas - 1, 1)

                if new_replicas < current_replicas:
                    print("📉 RL Scaling DOWN")
                    scale_deployment(name, namespace, new_replicas)
                    last_scale_time[key] = now

        else:
            print("✅ Optimal")

    time.sleep(INTERVAL)
