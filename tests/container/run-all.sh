#!/bin/bash
# Installs this copy of Power Mail-in-a-Box in fresh systemd containers, one per
# supported operating system (in parallel), and runs the test suites on each:
#   verify.sh         functional checks (mail, webmail, Nextcloud, Z-Push, panel, backups...)
#   services-test.sh  turning the optional services off and on
#   access-test.sh    restricting web applications to local access (run from the host)
#
# usage: tests/container/run-all.sh [-u <git ref>] [os...]
#   os         debian-12 debian-13 ubuntu-22.04 ubuntu-24.04 (default: all of them)
#   -u <ref>   test an upgrade: first install <ref> (e.g. origin/main or v61.1), then
#              run the setup of this copy over it
#
# Logs go to $LOGDIR (default /tmp/pmiab-tests). The containers (pmiab-test-<os>) are
# kept for inspection until the next run. Exits with status 1 if anything failed.
# Needs Docker and about 5 GB of disk per operating system; takes 20-40 minutes.

HERE=$(cd "$(dirname "$0")" && pwd)
REPO=$(cd "$HERE/../.." && pwd)
LOGDIR=${LOGDIR:-/tmp/pmiab-tests}

UPGRADE_FROM=
if [ "${1:-}" = "-u" ]; then UPGRADE_FROM=$2; shift 2; fi
OSES=${*:-debian-12 debian-13 ubuntu-22.04 ubuntu-24.04}

# Start Docker if it isn't running (e.g. in a fresh cloud container).
if ! docker info >/dev/null 2>&1; then
	(nohup dockerd > /tmp/dockerd.log 2>&1 &)
	for i in $(seq 1 30); do docker info >/dev/null 2>&1 && break; sleep 1; done
fi
docker info >/dev/null 2>&1 || { echo "Docker is not available."; exit 1; }

"$HERE/build-images.sh" $OSES || exit 1

rm -rf "$LOGDIR" && mkdir -p "$LOGDIR"
if [ -n "$UPGRADE_FROM" ]; then
	mkdir "$LOGDIR/base"
	git -C "$REPO" archive "$UPGRADE_FROM" | tar -x -C "$LOGDIR/base" || exit 1
fi

test_os() {
	local os=$1 name=pmiab-test-$1 log=$LOGDIR/$1
	docker rm -f $name >/dev/null 2>&1
	if [ -n "$UPGRADE_FROM" ]; then
		"$HERE/run-setup.sh" $name pmiab-$os "$LOGDIR/base" > $log-setup-base.txt 2>&1
		if ! grep -q "SETUP-EXIT-CODE: 0" $log-setup-base.txt; then
			echo "RESULT: setup of $UPGRADE_FROM failed" > $log-verify.txt
			return
		fi
	fi
	"$HERE/run-setup.sh" $name pmiab-$os "$REPO" > $log-setup.txt 2>&1
	if ! grep -q "SETUP-EXIT-CODE: 0" $log-setup.txt; then
		echo "RESULT: setup failed" > $log-verify.txt
		return
	fi
	docker cp "$HERE/verify.sh" $name:/root/verify.sh
	docker cp "$HERE/services-test.sh" $name:/root/services-test.sh
	timeout 1800 docker exec $name bash /root/verify.sh > $log-verify.txt 2>&1
	timeout 1800 docker exec $name bash /root/services-test.sh > $log-services.txt 2>&1
	timeout 1800 bash "$HERE/access-test.sh" $name > $log-access.txt 2>&1
}

for os in $OSES; do
	test_os $os &
	sleep 20 # don't hit the package mirrors all at the same time
done
wait

# Summary.
status=0
echo
echo "| Operating system | Setup | Functional | Services | Local-only access |"
echo "|---|---|---|---|---|"
for os in $OSES; do
	row="| $os |"
	if [ -n "$UPGRADE_FROM" ]; then
		grep -q "SETUP-EXIT-CODE: 0" $LOGDIR/$os-setup-base.txt 2>/dev/null && row="$row $UPGRADE_FROM → this:" || row="$row $UPGRADE_FROM: failed"
	fi
	grep -q "SETUP-EXIT-CODE: 0" $LOGDIR/$os-setup.txt 2>/dev/null && row="$row ok |" || { row="$row failed |"; status=1; }
	for suite in verify services access; do
		result=$(grep -h "^RESULT" $LOGDIR/$os-$suite.txt 2>/dev/null | sed 's/RESULT: //')
		passed=$(echo "$result" | sed -n 's/^\([0-9]*\) passed, \([0-9]*\) failed$/\1/p')
		failed=$(echo "$result" | sed -n 's/^\([0-9]*\) passed, \([0-9]*\) failed$/\2/p')
		if [ -n "$passed" ] && [ "$failed" = 0 ]; then
			row="$row $passed/$passed |"
		else
			row="$row ${result:-not run} |"
			status=1
		fi
	done
	echo "$row"
done
echo
for os in $OSES; do
	grep -h "^FAIL" $LOGDIR/$os-*.txt 2>/dev/null | sed "s/^/$os: /"
	grep -h "^RESULT: setup" $LOGDIR/$os-verify.txt 2>/dev/null | sed "s/^/$os: /"
done
echo "Logs: $LOGDIR"
exit $status
