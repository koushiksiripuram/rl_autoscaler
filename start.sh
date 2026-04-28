#!/bin/bash

echo "Starting RL Autoscaler..."

# Run autoscaler in background
python metrics.py &

echo "Starting Dashboard..."

# Run Streamlit in foreground
python -m streamlit run dashboard.py --server.port=8501 --server.address=0.0.0.0
