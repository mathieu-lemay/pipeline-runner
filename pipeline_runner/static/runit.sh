#!/bin/sh

pipelines &> /dev/fd/1 &

ARGS="--authorization-plugin=pipelines \
    --storage-driver=overlay2 \
    --registry-mirror http://${DOCKER_REGISTRY_MIRROR_HOST}:${DOCKER_REGISTRY_MIRROR_PORT} \
    --userns-remap=default \
    --log-level warn"

exec dockerd-entrypoint.sh $ARGS $@ &> /dev/fd/1
