#!/bin/bash
# Builds the container images the tests install Power Mail-in-a-Box in:
# pmiab-debian-12, pmiab-debian-13, pmiab-ubuntu-22.04 and pmiab-ubuntu-24.04.
# usage: build-images.sh [--force] [os...]
set -e
cd "$(dirname "$0")"

FORCE=
if [ "${1:-}" = "--force" ]; then FORCE=1; shift; fi
OSES=${*:-debian-12 debian-13 ubuntu-22.04 ubuntu-24.04}

# Trust the CA certificates of an HTTPS proxy the host goes through, if any
# (CA_BUNDLE, or the bundle of the Claude Code cloud environment).
CA_BUNDLE=${CA_BUNDLE:-/root/.ccr/ca-bundle.crt}
rm -rf cas && mkdir cas
if [ -f "$CA_BUNDLE" ]; then
	awk '/BEGIN CERTIFICATE/ { n++ } { if (n) print > ("cas/c" n ".crt") }' "$CA_BUNDLE"
fi

for os in $OSES; do
	case $os in
		debian-12) base=debian:bookworm ;;
		debian-13) base=debian:trixie ;;
		ubuntu-22.04) base=ubuntu:22.04 ;;
		ubuntu-24.04) base=ubuntu:24.04 ;;
		*) echo "Unknown operating system: $os"; exit 1 ;;
	esac
	if [ -z "$FORCE" ] && docker image inspect pmiab-$os >/dev/null 2>&1; then
		continue
	fi
	echo "Building pmiab-$os..."
	docker build -q --build-arg BASE=$base -t pmiab-$os . >/dev/null
done
rm -rf cas
