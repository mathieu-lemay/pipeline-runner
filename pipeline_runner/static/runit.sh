#!/bin/sh

if [ -d /opt/atlassian/pipelines/agent/bin ]; then
  cp /usr/local/bin/docker /opt/atlassian/pipelines/agent/bin
fi

pipelines &> /dev/fd/1 &

ARGS="--authorization-plugin=pipelines \
    --storage-driver=overlay2 \
    --registry-mirror http://${DOCKER_REGISTRY_MIRROR_HOST}:${DOCKER_REGISTRY_MIRROR_PORT} \
    --userns-remap=default \
    --log-level ${DOCKER_ENGINE_LOG_LEVEL:-warn}"

exec dockerd-entrypoint.sh $ARGS "$@" &> /dev/fd/1
