#!/bin/bash
# Installs (or upgrades) Power Mail-in-a-Box from a directory in a test container,
# creating the container if it doesn't exist.
# usage: run-setup.sh <container> <image> <source directory>
NAME=$1; IMAGE=$2; SRC=$3
HERE=$(cd "$(dirname "$0")" && pwd)

if ! docker inspect $NAME >/dev/null 2>&1; then
	docker run -d --name $NAME --hostname box.example.com --privileged --cgroupns=host \
		-v /sys/fs/cgroup:/sys/fs/cgroup:rw --tmpfs /run --tmpfs /run/lock $IMAGE >/dev/null
	sleep 6
fi
docker exec $NAME rm -rf /root/mailinabox
docker cp "$SRC" $NAME:/root/mailinabox
docker cp "$HERE/patch-for-container.sh" $NAME:/root/patch-for-container.sh
docker exec $NAME bash /root/patch-for-container.sh

IP=$(docker exec $NAME hostname -I | awk '{print $1}')
docker exec -e NONINTERACTIVE=1 -e PRIMARY_HOSTNAME=box.example.com -e PUBLIC_IP=$IP -e PUBLIC_IPV6= \
	-e PRIVATE_IP=$IP -e PRIVATE_IPV6= -e SKIP_NETWORK_CHECKS=1 -e DISABLE_FIREWALL=1 \
	-e EMAIL_ADDR=me@example.com -e EMAIL_PW='Secret123!pw' \
	-e REQUESTS_CA_BUNDLE=/etc/ssl/certs/ca-certificates.crt -e PIP_CERT=/etc/ssl/certs/ca-certificates.crt \
	-w /root/mailinabox $NAME bash -c 'setup/start.sh' 2>&1
echo "SETUP-EXIT-CODE: $?"
