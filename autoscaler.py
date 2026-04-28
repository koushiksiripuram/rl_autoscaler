import time
from kube_helper import scale_deployment


def compute_desired_replicas(cpu_util, mem_util, rps, current_replicas,
                             target_cpu, target_mem, rps_per_pod,
                             min_rep, max_rep):

    cpu_desired = int((cpu_util / target_cpu) * current_replicas) if cpu_util else current_replicas
    mem_desired = int((mem_util / target_mem) * current_replicas) if mem_util else current_replicas
    rps_desired = int(rps / rps_per_pod) + 1 if rps else current_replicas

    desired = max(cpu_desired, mem_desired, rps_desired)
    desired = max(min_rep, min(max_rep, desired))

    return desired, cpu_desired, mem_desired, rps_desired


def run_autoscaler(
    dep_name,
    agent,
    avg_cpu,
    avg_mem,
    cpu_util,
    mem_util,
    rps,
    current_replicas,
    last_scale_time,
    cooldown,
    target_cpu,
    target_mem,
    rps_per_pod,
    min_rep,
    max_rep
):
    now = time.time()

    # -------- Desired replicas --------
    desired, cpu_d, mem_d, rps_d = compute_desired_replicas(
        cpu_util, mem_util, rps, current_replicas,
        target_cpu, target_mem,
        rps_per_pod, min_rep, max_rep
    )

    print(f"Desired → CPU:{cpu_d} MEM:{mem_d} RPS:{rps_d} FINAL:{desired}")

    # -------- Cooldown --------
    if now - last_scale_time < cooldown:
        print("⏳ Cooldown active")
        return last_scale_time

    # -------- SCALE UP --------
    if desired > current_replicas:
        print("📈 Scaling UP")
        scale_deployment(dep_name, desired)
        return now

    # -------- SCALE DOWN (RL) --------
    elif desired < current_replicas:

        state = agent.build_state(
            current_replicas,
            avg_cpu,
            avg_mem,
            rps,
            now - last_scale_time
        )

        action = agent.act(state, training=True)

        if action == 1:
            new_replicas = max(current_replicas - 1, min_rep)

            if new_replicas < current_replicas:
                print("📉 RL Scaling DOWN")
                scale_deployment(dep_name, new_replicas)
                return now

    # -------- NO CHANGE --------
    print("✅ Optimal")
    return last_scale_time
