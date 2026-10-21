#!/usr/local/lib/mailinabox/env/bin/python
#
# Optional services (Nextcloud, Z-Push, Roundcube and Munin) that the
# administrator can turn off from the control panel.
#
# Every service is always installed and kept up to date by the setup scripts,
# so that turning one on again is instant and never leaves outdated software
# behind. Turning a service off stops serving it (its nginx locations are left
# out of the web configuration) and stops its background jobs. Its data is
# kept and is still backed up.
#
# The administrator's choice is stored in the "services" key of
# $STORAGE_ROOT/settings.yaml. The effective state is also written to
# /var/lib/mailinabox/services.json so that the PHP applications (Roundcube
# and Z-Push) can read it.
########################################################################

import json
import os
import re
import shutil
import sys
import uuid

import utils

SERVICES = {
	"nextcloud": {
		"title": "Contacts & Calendar (Nextcloud)",
		"description":
		"Contacts, calendars and files at /cloud, CardDAV/CalDAV for mail and mobile clients, and the address book of the webmail.",
		"paths": ["/cloud"],
		"installed": "/usr/local/lib/owncloud/occ",
	},
	"zpush": {
		"title": "Exchange ActiveSync (Z-Push)",
		"description":
		"Push synchronization of mail, contacts and calendars for mobile devices and Outlook (Exchange ActiveSync and autodiscover).",
		"paths": ["/Microsoft-Server-ActiveSync", "/autodiscover/autodiscover.xml"],
		"installed": "/usr/local/lib/z-push/index.php",
	},
	"webmail": {
		"title": "Webmail (Roundcube)",
		"description": "Read and send mail from a web browser at /mail.",
		"paths": ["/mail"],
		"installed": "/usr/local/lib/roundcubemail/public_html/index.php",
	},
	"munin": {
		"title": "Monitoring (Munin)",
		"description":
		"Graphs of the resource usage of the box, collected every five minutes.",
		"paths": ["/admin/munin"],
		"installed": "/usr/sbin/munin-node",
	},
}

STATE_FILE = "/var/lib/mailinabox/services.json"
DISABLED_CRON_DIR = "/var/lib/mailinabox/cron.disabled"

CRON_JOBS = {
	"nextcloud": ["mailinabox-nextcloud"],
	"munin": ["munin", "munin-node"],
}
SYSTEMD_UNITS = {
	"munin": ["munin-node.service", "munin.service"],
}


def is_installed(name):
	return os.path.exists(SERVICES[name]["installed"])


def get_enabled_services(env):
	# Returns {service name: True/False}, as set by the administrator.
	# Services are enabled unless they were turned off.
	config = utils.load_settings(env).get("services")
	if not isinstance(config, dict):
		config = {}
	return {name: config.get(name, True) is not False for name in SERVICES}


def is_enabled(name, env):
	return get_enabled_services(env)[name]


def get_services_status(env):
	enabled = get_enabled_services(env)
	return [{
		"name": name,
		"title": service["title"],
		"description": service["description"],
		"paths": service["paths"],
		"enabled": enabled[name],
		"installed": is_installed(name),
	} for name, service in SERVICES.items()]


def set_service_enabled(name, enabled, env):
	if name not in SERVICES:
		raise ValueError("Unknown service: %s" % name)
	if enabled and not is_installed(name):
		raise ValueError(
			"%s is not installed. Run 'sudo mailinabox' on the box to install it."
			% SERVICES[name]["title"])

	config = utils.load_settings(env)
	services = config.get("services")
	if not isinstance(services, dict):
		services = {}
	services[name] = bool(enabled)
	config["services"] = services
	utils.write_settings(config, env)

	return apply_services(env)


def filter_template(text, enabled):
	# Remove the blocks of the configuration templates in conf/ that belong to
	# disabled services. They are marked with lines like these (nginx, or XML):
	#   # BEGIN SERVICE nextcloud           <!-- BEGIN SERVICE nextcloud -->
	#   ...                                 ...
	#   # END SERVICE nextcloud             <!-- END SERVICE nextcloud -->
	def strip(m):
		return "" if not enabled.get(m.group(1), True) else m.group(0)

	return re.sub(
		r"^[ \t]*(?:#|<!--) BEGIN SERVICE (\w+)\b.*?\n.*?^[ \t]*(?:#|<!--) END SERVICE \1\b[^\n]*\n",
		strip,
		text,
		flags=re.MULTILINE | re.DOTALL)


def write_mobileconfig(env, enabled):
	# The iOS/macOS configuration profile served at /mailinabox.mobileconfig.
	# It only sets up the contacts and calendar accounts when Nextcloud is on.
	hostname = env["PRIMARY_HOSTNAME"]
	with open(os.path.join(os.path.dirname(__file__),
						"../conf/ios-profile.xml")) as f:
		profile = filter_template(f.read(), enabled)
	profile = re.sub(r"^[ \t]*<!-- (BEGIN|END) SERVICE \w+ -->\n", "", profile,
					flags=re.MULTILINE)
	profile = profile.replace("PRIMARY_HOSTNAME", hostname)
	for i in range(1, 5):
		# Stable identifiers, so that the profile only changes when its
		# contents do.
		profile = profile.replace(
			"UUID%d" % i,
			str(uuid.uuid5(uuid.NAMESPACE_DNS, "%d.mobileconfig.%s" % (i, hostname))))
	write_file("/var/lib/mailinabox/mobileconfig.xml", profile)


def write_file(fn, content):
	# Writes the file atomically, if it changed. Returns whether it changed.
	if os.path.exists(fn):
		with open(fn) as f:
			if f.read() == content:
				return False
	os.makedirs(os.path.dirname(fn), exist_ok=True)
	with open(fn + ".tmp", "w") as f:
		f.write(content)
	os.chmod(fn + ".tmp", 0o644)
	os.replace(fn + ".tmp", fn)
	return True


def set_cron_job(name, enabled):
	# Turned off cron jobs are moved out of /etc/cron.d, and back when they
	# are turned on again. The setup scripts always (re)write the job in
	# /etc/cron.d, which then takes precedence over a saved copy.
	active = os.path.join("/etc/cron.d", name)
	saved = os.path.join(DISABLED_CRON_DIR, name)
	if enabled:
		if os.path.exists(active):
			if os.path.exists(saved):
				os.unlink(saved)
		elif os.path.exists(saved):
			shutil.move(saved, active)
	elif os.path.exists(active):
		os.makedirs(DISABLED_CRON_DIR, exist_ok=True)
		shutil.move(active, saved)


def set_systemd_unit(unit, enabled):
	code, _ = utils.shell("check_output",
						["systemctl", "cat", unit],
						capture_stderr=True,
						trap=True)
	if code != 0:
		return  # not installed
	utils.shell("check_output",
				["systemctl", "enable" if enabled else "disable", "--now", unit],
				capture_stderr=True,
				trap=True)


def apply_services(env):
	# Makes the system match the enabled services, except for the nginx
	# configuration, which web_update.py writes. Returns a description of
	# what was done.
	enabled = get_enabled_services(env)

	write_file(STATE_FILE, json.dumps(enabled, indent=2) + "\n")
	write_mobileconfig(env, enabled)

	for name, jobs in CRON_JOBS.items():
		for job in jobs:
			set_cron_job(job, enabled[name])
	for name, units in SYSTEMD_UNITS.items():
		for unit in units:
			set_systemd_unit(unit, enabled[name])

	return "".join("%s: %s\n" % (SERVICES[name]["title"],
								"on" if enabled[name] else "off")
				for name in SERVICES)


if __name__ == "__main__":
	env = utils.load_environment()
	if len(sys.argv) == 3 and sys.argv[1] == "is-enabled" and sys.argv[2] in SERVICES:
		# Exits with status 0 if the service is enabled.
		sys.exit(0 if is_enabled(sys.argv[2], env) else 1)
	elif len(sys.argv) == 2 and sys.argv[1] == "apply":
		print(apply_services(env), end="")
	elif len(sys.argv) == 2 and sys.argv[1] == "list":
		for service in get_services_status(env):
			print("%-10s %-4s %s" % (service["name"], "on" if
									service["enabled"] else "off", service["title"]))
	else:
		print("Usage: %s apply | list | is-enabled {%s}" %
			(sys.argv[0], ",".join(SERVICES)))
		sys.exit(2)
