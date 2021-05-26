#! /bin/sh

if [ $# -lt 3 ]; then
    echo "Usage: $0 <retries> <delay> command ..." >&2
    exit 1
fi

retries=$1
wait=$2
shift 2

msg="$*"
[ ${#msg} -gt 80 ] && msg="$(echo "${msg}" | cut -c 1-77)..."

count=0
until "$@"; do
    exit=$?
    count=$((count + 1))


    if [ ${count} -lt "${retries}" ]; then
        echo "Attempt ${count}/${retries} for command '${msg}' exited with code ${exit}, retrying in ${wait} seconds..."
        sleep "${wait}"
    else
        echo "Attempt ${count}/${retries} for command '${msg}' exited with code ${exit}, no more retries left."
        exit ${exit}
    fi
done
