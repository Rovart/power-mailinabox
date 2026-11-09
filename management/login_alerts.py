#!/usr/local/lib/mailinabox/env/bin/python
#
# Successful logins from new places (control panel: Mail > Filtering).
#
# Reads the successful logins in the mail log (sending mail on ports 465/587, IMAP,
# POP3 and Sieve), remembers the networks each account logs in from, and prints the
# ones that are new. The cron job (see setup/management.sh) runs this every hour and
# pipes the output to email_administrator.py, so the administrator gets an email,
# like the backup and status check reports, when an account logs in from somewhere it
# never did before: a stolen password shows up there.
#
# * The email is about new operator blocks: the /16 of an IPv4 address or the /32 of an
#   IPv6 address (the size of a typical allocation to an internet provider). Mobile
#   connections get a new address, and a new IPv6 /64, nearly every time they connect,
#   but they stay in the operator's blocks, so a phone moving around doesn't send an
#   email every day, while a login through another provider or from another country
#   does. A login from an operator the account already uses isn't reported, even if a
#   stranger made it.
# * What is remembered, and trusted, is the smaller network: the /24 of an IPv4 address
#   or the /64 of an IPv6 address.
# * Logins from the box itself (the webmail, Z-Push and Nextcloud log in to IMAP and
#   SMTP from 127.0.0.1) are left out: those web apps have their own logs.
# * The first run only learns the networks in the current logs, without an email.
# * Networks not seen for 180 days are forgotten (and would be reported again).
#
# The networks used in the last 30 days are also trusted elsewhere: they are never
# banned by the miab-postfix-slow fail2ban jail (added to its ignore list here) and
# never blocked by the IP blocklists (see ip_blocklists.py).
#
# Where a network is: the city, country and internet provider (autonomous system) of
# the last address seen in it, from RIPEstat (stat.ripe.net, the RIPE NCC's public
# API, which serves MaxMind's GeoLite data). Only the address is sent, never the
# account. Looked up once per network by the hourly run (at most LOOKUPS_PER_RUN at a
# time) and kept with it, so the control panel never waits for it. A city is a guess:
# for mobile connections it is where the operator's network meets the internet, not
# where the phone is. Can be turned off in the control panel ("locate" in the
# "login_alerts" key).
#
# The emails can be turned off in the control panel ("login_alerts" key of
# $STORAGE_ROOT/settings.yaml); the networks are still learned.
########################################################################

import datetime
import glob
import gzip
import ipaddress
import json
import os
import re
import subprocess
import sys

import utils

MAIL_LOG = "/var/log/mail.log"
STATE_DIR = "/var/lib/mailinabox"
STATE_FILE = STATE_DIR + "/known-logins.json"
FORGET_DAYS = 180
TRUST_DAYS = 30
FAIL2BAN_JAIL = "miab-postfix-slow"
RIPESTAT = "https://stat.ripe.net/data/%s/data.json?resource=%s&sourceapp=power-mailinabox"
LOOKUPS_PER_RUN = 40
LOOKUP_TIMEOUT = 8

POSTFIX_RE = re.compile(
	r"postfix/submission/smtpd\[\d+\]: \w+: client=[^\[\s]*\[([0-9A-Fa-f.:]+)\](?::\d+)?, sasl_method=\S+, sasl_username=(\S+)")
# Dovecot writes to the mail log itself (log_path), as "imap-login: Info: Login: ...";
# through syslog the line is "dovecot: imap-login: Login: ..." instead.
DOVECOT_RE = re.compile(
	r"(imap|pop3|managesieve)-login: (?:Info: )?Login: user=<([^>]*)>, method=\S+, rip=([0-9A-Fa-f.:]+)")
SERVICES = {"imap": "IMAP", "pop3": "POP3", "managesieve": "Sieve"}


def now():
	return datetime.datetime.now(datetime.timezone.utc)


def network_of(address):
	ip = ipaddress.ip_address(address)
	if ip.version == 6 and ip.ipv4_mapped:
		ip = ip.ipv4_mapped
	prefix = 24 if ip.version == 4 else 64
	return ip, ipaddress.ip_network("%s/%d" % (ip, prefix), strict=False)


def block_of(network):
	# The operator block a network is in (see the top of this file).
	network = ipaddress.ip_network(network)
	return network.supernet(new_prefix=16 if network.version == 4 else 32)


def known_blocks(networks):
	blocks = set()
	for network in networks:
		try:
			blocks.add(block_of(network))
		except ValueError:
			pass
	return blocks


SYSLOG_TIME_RE = re.compile(r"^([A-Z][a-z]{2})\s+(\d{1,2}) (\d{2}):(\d{2}):(\d{2}) ")
ISO_TIME_RE = re.compile(r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(?:\.\d+)?(Z|[+-]\d{2}:?\d{2})? ")
MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def line_time(line, reference=None):
	# When a log line was written, as an ISO 8601 string in UTC, or None. Lines start
	# with a syslog time without a year ("Nov  9 10:00:01", also Dovecot's own log) or
	# an ISO 8601 time (rsyslog's default on newer Debian). Times without a time zone are
	# the box's local time; the year is the one that puts the time in the past.
	reference = reference or now()
	m = ISO_TIME_RE.match(line)
	if m:
		try:
			zone = m.group(2) or ""
			if zone == "Z":
				zone = "+00:00"
			elif zone and ":" not in zone:
				zone = zone[:3] + ":" + zone[3:]
			when = datetime.datetime.fromisoformat(m.group(1) + zone)
			if when.tzinfo is None:
				when = when.astimezone()
			return when.astimezone(datetime.timezone.utc).isoformat(timespec="seconds")
		except ValueError:
			return None
	m = SYSLOG_TIME_RE.match(line)
	if not m or m.group(1) not in MONTHS:
		return None
	month, day, hour, minute, second = MONTHS.index(m.group(1)) + 1, *map(int, m.groups()[1:])
	for year in (reference.year, reference.year - 1):
		try:
			when = datetime.datetime(year, month, day, hour, minute, second).astimezone()
		except ValueError:
			continue  # e.g. Feb 29
		if when <= reference + datetime.timedelta(days=1):
			return when.astimezone(datetime.timezone.utc).isoformat(timespec="seconds")
	return None


def parse_line(line):
	# Returns (user, address, service) for a successful login, or None.
	if "sasl_username=" in line:
		m = POSTFIX_RE.search(line)
		if m:
			return m.group(2).lower(), m.group(1), "SMTP"
	elif "Login: user=<" in line:
		m = DOVECOT_RE.search(line)
		if m:
			return m.group(2).lower(), m.group(3), SERVICES[m.group(1)]
	return None


def load_state():
	try:
		with open(STATE_FILE) as f:
			state = json.load(f)
		if isinstance(state, dict) and isinstance(state.get("users"), dict):
			return state
	except (OSError, ValueError):
		pass
	return None


def save_state(state):
	os.makedirs(STATE_DIR, exist_ok=True)
	with open(STATE_FILE + ".tmp", "w") as f:
		json.dump(state, f, indent=1, sort_keys=True)
	os.chmod(STATE_FILE + ".tmp", 0o600)
	os.replace(STATE_FILE + ".tmp", STATE_FILE)


def read_new_lines(position):
	# The lines added to the mail log since the last run. position is the inode and
	# offset of the log then; after the log was rotated, the rest of the old file
	# (mail.log.1) is read first. Returns (lines, new position).
	lines = []
	try:
		st = os.stat(MAIL_LOG)
	except OSError:
		return [], position
	inode, offset = (position or {}).get("inode"), (position or {}).get("offset", 0)
	if inode is not None and inode != st.st_ino:
		rotated = MAIL_LOG + ".1"
		try:
			if os.stat(rotated).st_ino == inode:
				with open(rotated, errors="replace") as f:
					f.seek(offset)
					lines.extend(f)
		except OSError:
			pass
		offset = 0
	elif inode is None or offset > st.st_size:
		offset = 0
	with open(MAIL_LOG, errors="replace") as f:
		f.seek(offset)
		while True:
			line = f.readline()
			if not line.endswith("\n"):
				break  # a line still being written is read next time
			lines.append(line)
			offset = f.tell()
	return lines, {"inode": st.st_ino, "offset": offset}


def is_local(ip, env):
	if ip.is_loopback:
		return True
	return str(ip) in {env.get(k) for k in ("PUBLIC_IP", "PUBLIC_IPV6", "PRIVATE_IP", "PRIVATE_IPV6")}


def record(state, lines, env):
	# Adds the logins in the lines to the state. Returns the (user, network) pairs of the
	# logins from operator blocks the user never used before.
	new = {}
	blocks = {user: known_blocks(networks) for user, networks in state["users"].items()}
	reference = now()
	default_stamp = reference.isoformat(timespec="seconds")
	for line in lines:
		login = parse_line(line)
		if not login:
			continue
		user, address, service = login
		# When the login happened, from the log line (not when this runs).
		stamp = line_time(line, reference) or default_stamp
		try:
			ip, network = network_of(address)
		except ValueError:
			continue
		if is_local(ip, env):
			continue
		networks = state["users"].setdefault(user, {})
		known = networks.get(str(network))
		if known is None:
			known = networks[str(network)] = {"first": stamp, "count": 0, "services": []}
			user_blocks = blocks.setdefault(user, set())
			if block_of(network) not in user_blocks:
				user_blocks.add(block_of(network))
				new[(user, str(network))] = known
		known["first"] = min(known.get("first") or stamp, stamp)
		if stamp >= known.get("last", ""):
			known["last"] = stamp
			known["address"] = str(ip)
		known["count"] += 1
		if service not in known["services"]:
			known["services"].append(service)
	return new


def all_mail_logs():
	# The mail log and its rotated copies, oldest first.
	def rotation(fn):
		m = re.search(r"\.(\d+)(\.gz)?$", fn)
		return -int(m.group(1)) if m else 0
	return sorted(glob.glob(MAIL_LOG + "*"), key=rotation)


def read_log(fn):
	try:
		opener = gzip.open if fn.endswith(".gz") else open
		with opener(fn, "rt", errors="replace") as f:
			return f.readlines()
	except (OSError, EOFError):
		return []


def fix_times(state):
	# Until v61.13 the first and last times of a network were when this script read the
	# log, not when the logins happened. Sets them once from all the mail logs (also the
	# rotated ones), for the networks already known. Counts are left as they are.
	reference = now()
	seen = {}
	for fn in all_mail_logs():
		for line in read_log(fn):
			login = parse_line(line)
			if not login:
				continue
			user, address, _ = login
			try:
				ip, network = network_of(address)
			except ValueError:
				continue
			known = state["users"].get(user, {}).get(str(network))
			stamp = line_time(line, reference)
			if known is None or stamp is None:
				continue
			times = seen.setdefault((user, str(network)), [stamp, stamp, str(ip)])
			if stamp < times[0]:
				times[0] = stamp
			if stamp >= times[1]:
				times[1], times[2] = stamp, str(ip)
	for (user, network), (first, last, address) in seen.items():
		known = state["users"][user][network]
		known["first"], known["last"], known["address"] = first, last, address
	state["times"] = "log"


def forget_old(state):
	limit = (now() - datetime.timedelta(days=FORGET_DAYS)).isoformat(timespec="seconds")
	for user in list(state["users"]):
		networks = state["users"][user]
		for network in list(networks):
			if networks[network].get("last", "") < limit:
				del networks[network]
		if not networks:
			del state["users"][user]


def get_recent_networks(days=TRUST_DAYS, state=None):
	state = state or load_state() or {"users": {}}
	limit = (now() - datetime.timedelta(days=days)).isoformat(timespec="seconds")
	networks = set()
	for user_networks in state["users"].values():
		for network, known in user_networks.items():
			if known.get("last", "") >= limit:
				networks.add(network)
	result = []
	for network in sorted(networks):
		try:
			result.append(ipaddress.ip_network(network))
		except ValueError:
			pass
	return result


def sync_fail2ban(networks):
	# Keeps the trusted networks in the ignore list of the slow submission jail. The
	# list is in memory only (fail2ban forgets it when it restarts), so this runs every
	# time. Returns quietly if fail2ban or the jail isn't there.
	try:
		out = subprocess.run(["fail2ban-client", "get", FAIL2BAN_JAIL, "ignoreip"],
							capture_output=True, text=True, timeout=30)
	except (OSError, subprocess.SubprocessError):
		return
	if out.returncode != 0:
		return
	current = set(re.findall(r"[0-9A-Fa-f.:]+/\d+|[0-9A-Fa-f]*[.:][0-9A-Fa-f.:]+", out.stdout))
	for network in networks:
		if str(network) not in current:
			subprocess.run(["fail2ban-client", "set", FAIL2BAN_JAIL, "addignoreip", str(network)],
						stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30)


def get_config(env):
	config = utils.load_settings(env).get("login_alerts")
	config = config if isinstance(config, dict) else {}
	return {
		"enabled": config.get("enabled") is not False,
		"locate": config.get("locate") is not False,
	}


def alerts_enabled(env):
	return get_config(env)["enabled"]


def set_config(env, **changes):
	config = utils.load_settings(env)
	current = get_config(env)
	current.update({k: bool(v) for k, v in changes.items()})
	config["login_alerts"] = current
	utils.write_settings(config, env)


def set_alerts_enabled(env, enabled):
	set_config(env, enabled=enabled)
	return "Emails about logins from new places are %s.\n" % ("on" if enabled else "off")


def set_locate_enabled(env, enabled):
	set_config(env, locate=enabled)
	return "Looking up where the addresses are is %s.\n" % ("on" if enabled else "off")


def ripestat(call, resource):
	import time
	import urllib.request
	request = urllib.request.Request(RIPESTAT % (call, resource),
									headers={"User-Agent": "Power-Mail-in-a-Box-login-alerts/1.0"})
	for attempt in range(2):
		try:
			with urllib.request.urlopen(request, timeout=LOOKUP_TIMEOUT) as response:
				return json.load(response).get("data") or {}
		except Exception:
			if attempt:
				raise
			time.sleep(2)


def provider_name(holder):
	# "Telefonica_de_EspaNa TELEFONICA DE ESPANA S.A.U." -> "TELEFONICA DE ESPANA S.A.U.",
	# "PACKETHUBSA-AS-AP - PacketHub S.A." -> "PacketHub S.A.": drop the AS handle.
	holder = holder.strip()
	if " - " in holder:
		return holder.split(" - ", 1)[1].strip()
	first, _, rest = holder.partition(" ")
	if rest and (re.search(r"[-_]", first) or re.match(r"^AS\d+$", first) or first.isupper()):
		return rest.strip()
	return holder


def lookup_location(address):
	# {"city", "country", "asn", "provider"} of an address (values may be None), or None
	# if RIPEstat can't be reached.
	try:
		geo = ripestat("maxmind-geo-lite", address)
		info = ripestat("network-info", address)
	except Exception:
		return None
	location = {"city": None, "country": None, "asn": None, "provider": None}
	try:
		found = geo["located_resources"][0]["locations"][0]
		location["city"] = found.get("city") or None
		location["country"] = found.get("country") or None
	except (KeyError, IndexError, TypeError):
		pass
	asns = info.get("asns") or []
	if asns:
		location["asn"] = int(asns[0])
		try:
			holder = ripestat("as-overview", "AS%d" % location["asn"]).get("holder") or ""
			location["provider"] = provider_name(holder)[:80] or None
		except Exception:
			pass
	return location


def locate_networks(state, env, only=None):
	# Looks up the networks that don't have a location yet (the ones in only first).
	if not get_config(env)["locate"]:
		return
	todo = []
	for user, networks in state["users"].items():
		for network, known in networks.items():
			if "location" not in known and known.get("address"):
				todo.append(((user, network) not in (only or {}), known))
	# The new networks first, then the most recently used.
	todo.sort(key=lambda item: item[1].get("last", ""), reverse=True)
	todo.sort(key=lambda item: item[0])
	cache = {}
	for _, known in todo[:LOOKUPS_PER_RUN]:
		address = known["address"]
		if address not in cache:
			cache[address] = lookup_location(address)
		if cache[address] is not None:
			known["location"] = cache[address]


def describe_location(location):
	if not location:
		return ""
	place = ", ".join(x for x in (location.get("city"), location.get("country")) if x)
	provider = location.get("provider") or ("AS%d" % location["asn"] if location.get("asn") else "")
	return " — ".join(x for x in (place, provider) if x)


def reverse_dns(address):
	try:
		import dns.resolver
		import dns.reversename
		resolver = dns.resolver.Resolver()
		resolver.lifetime = 3
		answer = resolver.resolve(dns.reversename.from_address(address), "PTR")
		return str(answer[0]).rstrip(".")
	except Exception:
		return None


def report(new):
	lines = [
		"These accounts logged in through an internet provider's block of addresses they",
		"hadn't used before (another provider, or another country):",
		"",
	]
	for (user, network), known in sorted(new.items()):
		host = reverse_dns(known["address"])
		where = describe_location(known.get("location"))
		lines.append("%s from %s (block %s)%s: %s, %d login%s" % (
			user, known["address"], block_of(network), " " + host if host else "",
			", ".join(known["services"]), known["count"], "" if known["count"] == 1 else "s"))
		if where:
			lines.append("    %s (approximate)" % where)
	lines += [
		"",
		"If you don't recognize one of them, change that account's password in the control",
		"panel (Mail > Users) and in its mail clients: someone else may know it.",
		"",
		"You can turn these emails off in the control panel (Mail > Filtering).",
	]
	return "\n".join(lines)


def run(env):
	state = load_state()
	first_run = state is None
	if first_run:
		state = {"users": {}, "log": None}
	lines, position = read_new_lines(state.get("log"))
	if first_run and os.path.exists(MAIL_LOG + ".1"):
		# Learn from the previous log too (no email on the first run anyway).
		with open(MAIL_LOG + ".1", errors="replace") as f:
			lines = list(f) + lines
	if not first_run and state.get("times") != "log":
		fix_times(state)
	state["times"] = "log"
	trusted_before = set(get_recent_networks(state=state))
	new = record(state, lines, env)
	forget_old(state)
	state["log"] = position
	save_state(state)
	locate_networks(state, env, only=new)
	save_state(state)

	trusted = get_recent_networks(state=state)
	sync_fail2ban(trusted)
	if set(trusted) != trusted_before:
		try:
			import ip_blocklists
			if ip_blocklists.get_settings(env)["enabled"] and ip_blocklists.is_available():
				ip_blocklists.apply(env)
		except Exception as e:
			print("Could not update the blocklists' exceptions: %s" % e, file=sys.stderr)

	if new and not first_run and alerts_enabled(env):
		return report(new)
	return ""


def get_status(env):
	state = load_state() or {"users": {}}
	limit = (now() - datetime.timedelta(days=TRUST_DAYS)).isoformat(timespec="seconds")
	recent = []
	for user, networks in state["users"].items():
		for network, known in networks.items():
			recent.append({
				"user": user,
				"network": network,
				"address": known.get("address"),
				"services": known.get("services", []),
				"first": known.get("first"),
				"last": known.get("last"),
				"count": known.get("count", 0),
				"block": str(block_of(network)),
				"location": known.get("location"),
				"trusted": known.get("last", "") >= limit,
			})
	recent.sort(key=lambda r: r.get("last") or "", reverse=True)
	config = get_config(env)
	return {
		"enabled": config["enabled"],
		"locate": config["locate"],
		"recent": recent[:2000],
		"forget_days": FORGET_DAYS,
		"trust_days": TRUST_DAYS,
	}


if __name__ == "__main__":
	env = utils.load_environment()
	if len(sys.argv) == 1:
		output = run(env)
		if output:
			print(output)
	elif len(sys.argv) == 2 and sys.argv[1] in ("on", "off"):
		print(set_alerts_enabled(env, sys.argv[1] == "on"), end="")
	elif len(sys.argv) == 3 and sys.argv[1] == "locate" and sys.argv[2] in ("on", "off"):
		print(set_locate_enabled(env, sys.argv[2] == "on"), end="")
	elif len(sys.argv) == 2 and sys.argv[1] == "status":
		print(json.dumps(get_status(env), indent=1))
	else:
		print("Usage: %s [on | off | locate on|off | status]" % sys.argv[0])
		sys.exit(2)
