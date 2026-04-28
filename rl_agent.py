import numpy as np

class RLAgent:
    def __init__(self, state_dim, action_dim, min_replicas, max_replicas,
                 cpu_request, mem_request, cooldown_seconds, rps_per_pod):

        self.min_replicas = min_replicas
        self.max_replicas = max_replicas
        self.cpu_request = cpu_request or 100
        self.mem_request = mem_request or 100
        self.cooldown = cooldown_seconds
        self.rps_per_pod = rps_per_pod

        self.exploration_rate = 0.3
        self.min_exploration = 0.02
        self.decay = 0.995

    def build_state(self, replicas, cpu, mem, rps, time_since):
        return np.array([
            replicas / self.max_replicas,
            min(cpu / self.cpu_request, 2.0),
            min(mem / self.mem_request, 2.0),
            min(rps / (self.rps_per_pod * self.max_replicas), 1.0),
            min(time_since / self.cooldown, 1.0)
        ], dtype=np.float32)

    def act(self, state):
        if np.random.rand() < self.exploration_rate:
            action = np.random.randint(2)
        else:
            cpu_util = state[1]
            rps = state[3]
            time_ok = state[4]

            if cpu_util < 0.5 and rps < 0.5 and time_ok > 0.3:
                action = 1
            else:
                action = 0

        self.exploration_rate = max(self.min_exploration,
                                    self.exploration_rate * self.decay)
        return action
