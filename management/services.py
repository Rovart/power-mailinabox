#!/usr/local/lib/mailinabox/env/bin/python
#
# Optional services (Nextcloud, Z-Push, Roundcube and Munin) that the
# administrator can turn off from the control panel.
#
# By default every service is installed and kept up to date by the setup
# scripts, even while it's turned off, so that turning one on again is instant
# and never leaves outdated software behind. Turning a service off stops
# serving it (its nginx locations are left out of the web configuration) and
# stops its background jobs. Its data is kept and is still backed up.
#
# Boxes that don't want to install and update software they don't use can turn
# that off ("update-turned-off no" below). Then the setup skips the web
# applications (Roundcube, Nextcloud and Z-Push) that are turned off, and marks
# the ones it skipped as outdated. An outdated service isn't served, even after
# it's turned on again, until the setup runs again and updates it.
#
# The administrator's choices are stored in the "services" and
# "update_turned_off_services" keys of $STORAGE_ROOT/settings.yaml. The
# services that are actually served are written to
# /var/lib/mailinabox/services.json so that the PHP applications (Roundcube and
# Z-Push) can read it. The choices are also written to
# /var/lib/mailinabox/services.env for the setup scripts, which run before this
# program can (see setup/functions.sh).
#
# The web applications can also be restricted to the box itself (e.g. through
# an SSH tunnel) and, optionally, to trusted networks: requests from anywhere
# else get "403 Forbidden". This is stored in the "web_access" key.
########################################################################

import ipaddress
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

# Web applications that can be restricted to local access. Their nginx locations
# are marked with "# ACCESS <name>" lines in the templates in conf/.
WEB_ACCESS_AREAS = {
	"admin": {
		"title": "Control panel & monitoring",
		"paths": ["/admin"],
	},
	"webmail": {
		"title": "Webmail (Roundcube)",
		"paths": ["/mail"],
	},
	"nextcloud": {
		"title": "Nextcloud & CalDAV/CardDAV",
		"paths": ["/cloud", "/caldav", "/carddav", "/webdav"],
	},
	"zpush": {
		"title": "Exchange ActiveSync (Z-Push)",
		"paths": ["/Microsoft-Server-ActiveSync", "/autodiscover/autodiscover.xml"],
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

SETUP_STATE_FILE = "/var/lib/mailinabox/services.env"
OUTDATED_FILE = "/var/lib/mailinabox/services.outdated"

# The services the setup can skip while they're turned off (see above). Munin
# is small and part of the system's monitoring, so it's always kept up to date.
SKIPPABLE_SERVICES = ("webmail", "nextcloud", "zpush")


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


def updates_turned_off_services(env):
	# Whether the setup installs and updates the services that are turned off
	# (the default).
	return utils.load_settings(env).get("update_turned_off_services",
										True) is not False


def set_updates_turned_off_services(update, env):
	config = utils.load_settings(env)
	config["update_turned_off_services"] = bool(update)
	utils.write_settings(config, env)
	return apply_services(env)


def get_outdated_services():
	# The services the setup skipped while they were turned off, and hasn't
	# installed or updated since (see optional_service_skipped in
	# setup/functions.sh).
	try:
		with open(OUTDATED_FILE) as f:
			return {name for name in f.read().split() if name in SERVICES}
	except FileNotFoundError:
		return set()


def get_served_services(env):
	# Returns {service name: True/False}: whether the service is served, which
	# is when it's turned on, installed and not outdated.
	enabled = get_enabled_services(env)
	outdated = get_outdated_services()
	return {
		name: enabled[name] and is_installed(name) and name not in outdated
		for name in SERVICES
	}


def can_wait_for_setup(name, env):
	# Whether a service that isn't installed (or is outdated) can be turned on,
	# to be served after the setup runs again and installs or updates it.
	return name in SKIPPABLE_SERVICES and not updates_turned_off_services(env)


def get_services_status(env):
	enabled = get_enabled_services(env)
	served = get_served_services(env)
	outdated = get_outdated_services()
	return [{
		"name": name,
		"title": service["title"],
		"description": service["description"],
		"paths": service["paths"],
		"enabled": enabled[name],
		"installed": is_installed(name),
		"served": served[name],
		"outdated": name in outdated,
		"can_wait_for_setup": can_wait_for_setup(name, env),
	} for name, service in SERVICES.items()]


def set_service_enabled(name, enabled, env):
	if name not in SERVICES:
		raise ValueError("Unknown service: %s" % name)
	if enabled and not is_installed(name) and not can_wait_for_setup(name, env):
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


def filter_template(text, enabled, access_rules=None):
	# Remove the blocks of the configuration templates in conf/ that belong to
	# disabled services. They are marked with lines like these (nginx, or XML):
	#   # BEGIN SERVICE nextcloud           <!-- BEGIN SERVICE nextcloud -->
	#   ...                                 ...
	#   # END SERVICE nextcloud             <!-- END SERVICE nextcloud -->
	def strip(m):
		return "" if not enabled.get(m.group(1), True) else m.group(0)

	text = re.sub(
		r"^[ \t]*(?:#|<!--) BEGIN SERVICE (\w+)\b.*?\n.*?^[ \t]*(?:#|<!--) END SERVICE \1\b[^\n]*\n",
		strip,
		text,
		flags=re.MULTILINE | re.DOTALL)

	# In nginx templates, "# ACCESS <area>" lines are replaced by the rules that
	# restrict the area to local access, if it is restricted.
	if access_rules is not None:

		def access(m):
			rules = access_rules.get(m.group(2))
			if not rules:
				return ""
			return "".join(m.group(1) + rule + "\n" for rule in rules)

		text = re.sub(r"^([ \t]*)# ACCESS (\w+)[^\n]*\n",
					access,
					text,
					flags=re.MULTILINE)

	return text


def get_box_addresses(env):
	# The addresses requests from the box itself come from.
	addresses = ["127.0.0.1", "::1"]
	for key in ("PUBLIC_IP", "PUBLIC_IPV6", "PRIVATE_IP", "PRIVATE_IPV6"):
		value = env.get(key)
		if value and value not in addresses:
			addresses.append(value)
	return addresses


def parse_networks(networks):
	# Validates and normalizes a list of IP addresses or networks (CIDR), given
	# as a list or as a string separated by spaces, commas or new lines.
	if isinstance(networks, str):
		networks = re.split(r"[\s,]+", networks)
	result = []
	for network in networks:
		network = network.strip()
		if not network:
			continue
		try:
			network = str(ipaddress.ip_network(network, strict=False))
		except ValueError:
			raise ValueError("%s is not a valid IP address or network." % network)
		if network not in result:
			result.append(network)
	return result


def get_web_access(env):
	# Returns which areas are restricted to local access, and the networks
	# that are allowed besides the box itself.
	config = utils.load_settings(env).get("web_access")
	if not isinstance(config, dict):
		config = {}
	local_only = config.get("local_only")
	if not isinstance(local_only, dict):
		local_only = {}
	try:
		allowed_networks = parse_networks(config.get("allowed_networks") or [])
	except ValueError:
		allowed_networks = []
	return {
		"local_only": {
			name: local_only.get(name) is True
			for name in WEB_ACCESS_AREAS
		},
		"allowed_networks": allowed_networks,
	}


def set_web_access(env, area=None, local_only=None, allowed_networks=None):
	# Restricts an area ("all" for every area) to local access, or opens it,
	# and/or sets the allowed networks.
	access = get_web_access(env)
	if area is not None:
		if area != "all" and area not in WEB_ACCESS_AREAS:
			raise ValueError("Unknown area: %s" % area)
		for name in (WEB_ACCESS_AREAS if area == "all" else [area]):
			access["local_only"][name] = bool(local_only)
	if allowed_networks is not None:
		access["allowed_networks"] = parse_networks(allowed_networks)

	config = utils.load_settings(env)
	config["web_access"] = access
	utils.write_settings(config, env)
	return access


def is_address_allowed(address, env):
	# Whether requests from this address are allowed to local-only areas.
	try:
		address = ipaddress.ip_address(address)
	except ValueError:
		return False
	if address.is_loopback:
		return True
	networks = get_box_addresses(env) + get_web_access(env)["allowed_networks"]
	for network in networks:
		network = ipaddress.ip_network(network, strict=False)
		if address.version == network.version and address in network:
			return True
	return False


def get_access_rules(env):
	# The nginx directives for each area that is restricted to local access.
	access = get_web_access(env)
	rules = ["allow %s;" % a for a in get_box_addresses(env)]
	rules += ["allow %s;" % n for n in access["allowed_networks"]]
	rules += ["deny all;"]
	return {
		name: ["# Only from the box itself and the allowed networks."] + rules
		for name, local_only in access["local_only"].items() if local_only
	}


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


def get_setup_state(env):
	# The administrator's choices, as shell variables for the setup scripts.
	enabled = get_enabled_services(env)
	lines = [
		"# Written by management/services.py for the setup scripts. Don't edit.",
		"UPDATE_TURNED_OFF_SERVICES=%d" % updates_turned_off_services(env),
	]
	lines += [
		"SERVICE_%s=%d" % (name.upper(), enabled[name]) for name in SERVICES
	]
	return "\n".join(lines) + "\n"


def describe_state(name, enabled, served):
	if served[name]:
		return "on"
	if enabled[name]:
		return "on after running 'sudo mailinabox' to install or update it"
	return "off"


def apply_services(env):
	# Makes the system match the enabled services, except for the nginx
	# configuration, which web_update.py writes. Returns a description of
	# what was done.
	enabled = get_enabled_services(env)
	served = get_served_services(env)

	write_file(STATE_FILE, json.dumps(served, indent=2) + "\n")
	write_file(SETUP_STATE_FILE, get_setup_state(env))
	write_mobileconfig(env, served)

	for name, jobs in CRON_JOBS.items():
		for job in jobs:
			set_cron_job(job, served[name])
	for name, units in SYSTEMD_UNITS.items():
		for unit in units:
			set_systemd_unit(unit, served[name])

	return "".join("%s: %s\n" % (SERVICES[name]["title"],
								describe_state(name, enabled, served))
				for name in SERVICES)


if __name__ == "__main__":
	env = utils.load_environment()
	if len(sys.argv) == 4 and sys.argv[1] == "web-access" and sys.argv[3] in ("local", "public"):
		# Works without the management daemon, e.g. to undo a restriction:
		#   management/services.py web-access all public
		set_web_access(env, sys.argv[2], sys.argv[3] == "local")
		from web_update import do_web_update
		do_web_update(env)
		for name, local_only in get_web_access(env)["local_only"].items():
			print("%-10s %s" % (name, "local only" if local_only else "public"))
	elif len(sys.argv) == 3 and sys.argv[1] == "is-enabled" and sys.argv[2] in SERVICES:
		# Exits with status 0 if the service is enabled.
		sys.exit(0 if is_enabled(sys.argv[2], env) else 1)
	elif len(sys.argv) == 2 and sys.argv[1] == "apply":
		print(apply_services(env), end="")
	elif len(sys.argv) == 3 and sys.argv[1] == "update-turned-off" and sys.argv[2] in ("yes", "no"):
		# Whether the setup installs and updates the services that are turned
		# off (see the top of this file).
		print(set_updates_turned_off_services(sys.argv[2] == "yes", env), end="")
	elif len(sys.argv) == 2 and sys.argv[1] == "list":
		for service in get_services_status(env):
			state = "on" if service["served"] else (
				"wait" if service["enabled"] else "off")
			print("%-10s %-4s %s" % (service["name"], state, service["title"]))
		if not updates_turned_off_services(env):
			print("Services that are turned off are not installed or updated by the setup.")
	else:
		print("Usage: %s apply | list | is-enabled {%s} | update-turned-off {yes,no} | web-access {all,%s} {local,public}" %
			(sys.argv[0], ",".join(SERVICES), ",".join(WEB_ACCESS_AREAS)))
		sys.exit(2)
