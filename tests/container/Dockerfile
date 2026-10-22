# A minimal Debian/Ubuntu system running systemd, to install Power Mail-in-a-Box in.
# Built by build-images.sh.
ARG BASE
FROM ${BASE}
ENV container=docker DEBIAN_FRONTEND=noninteractive
# Extra CA certificates (e.g. of an HTTPS proxy the host goes through).
COPY cas/ /usr/local/share/ca-certificates/
RUN apt-get update -qq && apt-get install -y -qq --no-install-recommends systemd systemd-sysv dbus ca-certificates \
      locales lsb-release curl wget git sudo iproute2 procps nano less cron rsyslog openssh-client >/dev/null \
 && update-ca-certificates >/dev/null \
 && sed -i 's/^# *en_US.UTF-8/en_US.UTF-8/' /etc/locale.gen && locale-gen >/dev/null \
 && rm -f /etc/systemd/system/*.wants/getty* \
 && systemctl mask systemd-logind.service getty.target console-getty.service >/dev/null 2>&1 || true
STOPSIGNAL SIGRTMIN+3
CMD ["/sbin/init"]
