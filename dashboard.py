import streamlit as st
import requests
import subprocess
import json
import time
from kubernetes import client, config
from datetime import datetime

# ================= CONFIG =================
NAMESPACE = "default"
PROMETHEUS_URL = "http://prometheus.monitoring.svc.cluster.local:9090"
REFRESH_INTERVAL = 10
# ==========================================

st.set_page_config(
    page_title="K8s Autoscaler Dashboard",
    page_icon="🚀",
    layout="wide",
    initial_sidebar_state="collapsed"
)

st.markdown("""
<style>
    .main { background-color: #0e1117; }
    .action-scale-up {
        background: #1a3a1a;
        border-left: 4px solid #00ff88;
        border-radius: 8px;
        padding: 10px;
        margin: 5px 0;
    }
    .action-scale-down {
        background: #3a1a1a;
        border-left: 4px solid #ff4444;
        border-radius: 8px;
        padding: 10px;
        margin: 5px 0;
    }
    .service-url {
        background: #1e2130;
        border-radius: 8px;
        padding: 12px;
        border: 1px solid #00d4ff;
        font-size: 16px;
    }
</style>
""", unsafe_allow_html=True)


# -------- Kubernetes Helpers --------
@st.cache_resource
def load_k8s():
    try:
        config.load_incluster_config()
        print("Using in-cluster config")
    except Exception as e:
        print("Falling back:", e)
        config.load_kube_config()
    return client.AppsV1Api(), client.CoreV1Api()

def get_all_deployments(apps_v1):
    try:
        deps = apps_v1.list_namespaced_deployment(NAMESPACE)
        result = []
        for dep in deps.items:
            result.append({
                "name": dep.metadata.name,
                "namespace": dep.metadata.namespace,
                "desired": dep.spec.replicas,
                "ready": dep.status.ready_replicas or 0,
                "available": dep.status.available_replicas or 0,
                "unavailable": dep.status.unavailable_replicas or 0,
            })
        return result
    except:
        return []

def get_pods(core_v1, deployment_name):
    try:
        pods = core_v1.list_namespaced_pod(NAMESPACE, label_selector=f"app={deployment_name}")
        result = []
        for pod in pods.items:
            result.append({
                "name": pod.metadata.name,
                "status": pod.status.phase,
                "node": pod.spec.node_name or "N/A",
                "start_time": pod.status.start_time.strftime("%H:%M:%S") if pod.status.start_time else "N/A",
                "ip": pod.status.pod_ip or "N/A",
            })
        return result
    except:
        return []

def get_pod_metrics():
    try:
        cmd = ["kubectl", "get", "--raw",
               f"/apis/metrics.k8s.io/v1beta1/namespaces/{NAMESPACE}/pods"]
        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if result.returncode != 0:
            return []
        data = json.loads(result.stdout)
        return data.get("items", [])
    except:
        return []

def parse_cpu(cpu_str):
    cpu_str = str(cpu_str)
    if cpu_str.endswith("n"):
        return int(cpu_str[:-1]) / 1_000_000
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

def get_service_url():
    try:
        cmd = ["docker", "inspect", "-f",
               "{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}",
               "kind-control-plane"]
        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        ip = result.stdout.strip()
        return f"http://{ip}:30080" if ip else None
    except:
        return None

def query_prometheus(promql):
    try:
        resp = requests.get(f"{PROMETHEUS_URL}/api/v1/query",
                            params={"query": promql}, timeout=5)
        data = resp.json()
        if data["status"] == "success" and data["data"]["result"]:
            return float(data["data"]["result"][0]["value"][1])
    except:
        pass
    return None

def get_http_rps():
    return query_prometheus("rate(nginx_http_requests_total[1m])")

def get_prometheus_status():
    try:
        resp = requests.get(f"{PROMETHEUS_URL}/-/healthy", timeout=3)
        return resp.status_code == 200
    except:
        return False


# -------- Session State --------
if "action_log" not in st.session_state:
    st.session_state.action_log = []
if "prev_replicas" not in st.session_state:
    st.session_state.prev_replicas = {}

def log_action(action_type, message):
    st.session_state.action_log.insert(0, {
        "time": datetime.now().strftime("%H:%M:%S"),
        "type": action_type,
        "message": message,
    })
    if len(st.session_state.action_log) > 20:
        st.session_state.action_log = st.session_state.action_log[:20]


# ======== MAIN DASHBOARD ========
st.title("🚀 Kubernetes Autoscaler Dashboard")
st.caption(f"Namespace: `{NAMESPACE}` | Auto-refresh every {REFRESH_INTERVAL}s")

try:
    apps_v1, core_v1 = load_k8s()
    k8s_ok = True
except Exception as e:
    k8s_ok = False
    st.error(f"Kubernetes connection failed: {e}")

prom_ok = get_prometheus_status()

col1, col2, col3 = st.columns(3)
col1.metric("Kubernetes", "✅ Connected" if k8s_ok else "❌ Disconnected")
col2.metric("Prometheus", "✅ Connected" if prom_ok else "❌ Disconnected")
col3.metric("Last Updated", datetime.now().strftime("%H:%M:%S"))

st.divider()

if k8s_ok:
    all_deployments = get_all_deployments(apps_v1)
    pod_metrics = get_pod_metrics()
    http_rps = get_http_rps()
    service_url = get_service_url()

    # ---- All Deployments Summary ----
    st.subheader("📦 All Deployments")
    summary_data = []
    for d in all_deployments:
        prev = st.session_state.prev_replicas.get(d["name"])
        curr = d["desired"]
        if prev is not None:
            if curr > prev:
                log_action("scale_up", f"[{d['name']}] Scaled UP: {prev} → {curr} replicas")
            elif curr < prev:
                log_action("scale_down", f"[{d['name']}] Scaled DOWN: {prev} → {curr} replicas")
        st.session_state.prev_replicas[d["name"]] = curr

        summary_data.append({
            "Deployment": d["name"],
            "Desired": d["desired"],
            "Ready": d["ready"],
            "Available": d["available"],
            "Unavailable": d["unavailable"],
        })
    st.table(summary_data)

    st.divider()

    # ---- Deployment Switcher ----
    st.subheader("🔍 Deployment Detail")
    dep_names = [d["name"] for d in all_deployments]
    selected_dep = st.selectbox("Select a deployment to inspect:", dep_names)
    dep = next((d for d in all_deployments if d["name"] == selected_dep), {})

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Desired Replicas", dep.get("desired", "N/A"))
    c2.metric("Ready Pods", dep.get("ready", 0))
    c3.metric("Available Pods", dep.get("available", 0))
    c4.metric("Unavailable Pods", dep.get("unavailable", 0))

    st.divider()

    # ---- Live Metrics ----
    st.subheader("📊 Live Metrics")
    total_cpu, total_mem, pod_count = 0, 0, 0
    for pod in pod_metrics:
        if selected_dep in pod["metadata"]["name"]:
            pod_cpu = sum(parse_cpu(c["usage"]["cpu"]) for c in pod["containers"])
            pod_mem = sum(parse_memory(c["usage"]["memory"]) for c in pod["containers"])
            total_cpu += pod_cpu
            total_mem += pod_mem
            pod_count += 1

    avg_cpu = total_cpu / pod_count if pod_count else 0
    avg_mem = total_mem / pod_count if pod_count else 0
    cpu_util = (avg_cpu / 50) * 100
    mem_util = (avg_mem / 64) * 100

    m1, m2, m3 = st.columns(3)
    m1.metric("Avg CPU Usage", f"{avg_cpu:.2f}m", f"{cpu_util:.1f}% of request")
    m2.metric("Avg Memory Usage", f"{avg_mem:.1f}Mi", f"{mem_util:.1f}% of request")
    m3.metric("HTTP RPS", f"{http_rps:.2f}" if http_rps else "N/A")

    st.divider()

    # ---- Pod Table ----
    st.subheader("🐳 Pod Status")
    pods = get_pods(core_v1, selected_dep)
    if pods:
        pod_data = []
        for pod in pods:
            status_icon = "🟢" if pod["status"] == "Running" else "🟡"
            pod_data.append({
                "Pod Name": pod["name"],
                "Status": f"{status_icon} {pod['status']}",
                "Node": pod["node"],
                "Pod IP": pod["ip"],
                "Started At": pod["start_time"],
            })
        st.table(pod_data)
    else:
        st.info(f"No pods found for {selected_dep}")

    st.divider()

    # ---- Service URL ----
    st.subheader("🌐 Service Access")
    if service_url:
        st.markdown(f"""
        <div class="service-url">
            🔗 <b>Nginx Service URL:</b> <a href="{service_url}" target="_blank">{service_url}</a>
        </div>
        """, unsafe_allow_html=True)
    else:
        st.warning("Could not retrieve service URL.")

    st.divider()

    # ---- RL Action Log ----
    st.subheader("🤖 RL Model Action Log")
    if st.session_state.action_log:
        for entry in st.session_state.action_log:
            if entry["type"] == "scale_up":
                st.markdown(f"""<div class="action-scale-up">📈 <b>{entry['time']}</b> — {entry['message']}</div>""",
                            unsafe_allow_html=True)
            elif entry["type"] == "scale_down":
                st.markdown(f"""<div class="action-scale-down">📉 <b>{entry['time']}</b> — {entry['message']}</div>""",
                            unsafe_allow_html=True)
    else:
        st.info("No scaling actions recorded yet.")

    # ---- Grafana Embed ----
    with st.expander("📈 Grafana Dashboard (embed)"):
        grafana_url = st.text_input("Paste your Grafana panel embed URL here:", "")
        if grafana_url:
            st.components.v1.iframe(grafana_url, height=400)
        else:
            st.info("Enter your Grafana embed URL above to display live graphs here.")

st.divider()
st.caption("⏱ Dashboard auto-refreshes every 10 seconds")
time.sleep(REFRESH_INTERVAL)
st.rerun()
