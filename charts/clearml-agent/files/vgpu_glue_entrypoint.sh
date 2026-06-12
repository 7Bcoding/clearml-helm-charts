#!/bin/bash
# Replaces the final `python3 k8s_glue_example.py` in the stock entrypoint so the
# vGPU hook loads in the same Python process (PYTHONSTARTUP is interactive-only in Py3).
set -x

export CLEARML_FILES_HOST=${CLEARML_FILES_HOST:-$TRAINS_FILES_HOST}

if [ -z "$CLEARML_FILES_HOST" ]; then
  CLEARML_HOST_IP=${CLEARML_HOST_IP:-${TRAINS_HOST_IP:-$(curl -s https://ifconfig.me/ip)}}
fi

export CLEARML_FILES_HOST=${CLEARML_FILES_HOST:-${TRAINS_FILES_HOST:-"http://$CLEARML_HOST_IP:8081"}}
export CLEARML_WEB_HOST=${CLEARML_WEB_HOST:-${TRAINS_WEB_HOST:-"http://$CLEARML_HOST_IP:8080"}}
export CLEARML_API_HOST=${CLEARML_API_HOST:-${TRAINS_API_HOST:-"http://$CLEARML_HOST_IP:8008"}}

if [ -z "$CLEARML_AGENT_NO_UPDATE" ]; then
  if [ -n "$CLEARML_AGENT_UPDATE_REPO" ]; then
    python3 -m pip install -q -U "$CLEARML_AGENT_UPDATE_REPO"
  else
    python3 -m pip install -q -U "clearml-agent${CLEARML_AGENT_UPDATE_VERSION:-$TRAINS_AGENT_UPDATE_VERSION}"
  fi
fi

echo "api.credentials.access_key: ${CLEARML_API_ACCESS_KEY}" >> ~/clearml.conf
echo "api.credentials.secret_key: ${CLEARML_API_SECRET_KEY}" >> ~/clearml.conf
echo "api.api_server: ${CLEARML_API_HOST}" >> ~/clearml.conf
echo "api.web_server: ${CLEARML_WEB_HOST}" >> ~/clearml.conf
echo "api.files_server: ${CLEARML_FILES_HOST}" >> ~/clearml.conf

export PATH=$PATH:$HOME/bin
source /root/.bashrc

if [ -f ./provider_entrypoint.sh ]; then
  ./provider_entrypoint.sh
fi

exec python3 -u /root/vgpu/run_k8s_glue_with_vgpu_hook.py
