#!/bin/bash
# TEST-ONLY adjustments to the copy of Power Mail-in-a-Box inside a test container
# (run inside it by run-setup.sh before the setup). Docker containers differ from
# real servers in a few ways the setup can't handle.
set -e
cd /root/mailinabox
# 1. Docker networks usually have no IPv6: drop nginx's and Dovecot's IPv6 listeners.
sed -i '/listen \[::\]/d' conf/nginx.conf
mkdir -p /etc/dovecot
echo "listen = *" > /etc/dovecot/local.conf
# 2. pip inside virtualenvs must trust the CA certificates of the system (which may
#    include those of an HTTPS proxy, see build-images.sh).
mkdir -p /etc/pip && printf "[global]\ncert = /etc/ssl/certs/ca-certificates.crt\n" > /etc/pip.conf
# 3. /etc/resolv.conf is a bind mount in Docker: it can be overwritten but not removed.
sed -i 's|^rm -f /etc/resolv.conf$|: # (test) rm -f /etc/resolv.conf|' setup/system.sh
# 4. Sandboxed hosts may not allow direct (recursive) DNS queries: keep Docker's resolver.
sed -i 's|^echo "nameserver 127.0.0.1" > /etc/resolv.conf$|: # (test) keep the container resolver|' setup/system.sh
