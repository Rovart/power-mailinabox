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
# * A "network" is the /24 of an IPv4 address or the /64 of an IPv6 address, so that
#   a home connection whose address changes now and then doesn't send an email every
#   time. Mobile connections can still change networks often.
# * Logins from the box itself (the webmail, Z-Push and Nextcloud log in to IMAP and
#   SMTP from 127.0.0.1) are left out: those web apps have their own logs.
# * The first run only learns the networks in the current logs, without an email.
# * Networks not seen for 180 days are forgotten (and would be reported again).
#
# The networks used in the last 30 days are also trusted elsewhere: they are never
# banned by the miab-postfix-slow fail2ban jail (added to its ignore list here) and
# never blocked by the IP blocklists (see ip_blocklists.py).
#
# The emails can be turned off in the control panel ("login_alerts" key of
# $STORAGE_ROOT/settings.yaml); the networks are still learned.
########################################################################

import datetime
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
	# Adds the logins in the lines to the state. Returns the new (user, network) pairs.
	new = {}
	stamp = now().isoformat(timespec="seconds")
	for line in lines:
		login = parse_line(line)
		if not login:
			continue
		user, address, service = login
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
			new[(user, str(network))] = known
		known["last"] = stamp
		known["address"] = str(ip)
		known["count"] += 1
		if service not in known["services"]:
			known["services"].append(service)
	return new


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


def alerts_enabled(env):
	config = utils.load_settings(env).get("login_alerts")
	return not (isinstance(config, dict) and config.get("enabled") is False)


def set_alerts_enabled(env, enabled):
	config = utils.load_settings(env)
	config["login_alerts"] = {"enabled": bool(enabled)}
	utils.write_settings(config, env)
	return "Emails about logins from new places are %s.\n" % ("on" if enabled else "off")


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
		"These accounts logged in from a network they hadn't used before:",
		"",
	]
	for (user, network), known in sorted(new.items()):
		host = reverse_dns(known["address"])
		lines.append("%s from %s (network %s)%s: %s, %d login%s" % (
			user, known["address"], network, " " + host if host else "",
			", ".join(known["services"]), known["count"], "" if known["count"] == 1 else "s"))
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
	trusted_before = set(get_recent_networks(state=state))
	new = record(state, lines, env)
	forget_old(state)
	state["log"] = position
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
			if known.get("last", "") >= limit:
				recent.append({
					"user": user,
					"network": network,
					"address": known.get("address"),
					"services": known.get("services", []),
					"first": known.get("first"),
					"last": known.get("last"),
				})
	recent.sort(key=lambda r: r.get("last") or "", reverse=True)
	return {
		"enabled": alerts_enabled(env),
		"recent": recent[:200],
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
	elif len(sys.argv) == 2 and sys.argv[1] == "status":
		print(json.dumps(get_status(env), indent=1))
	else:
		print("Usage: %s [on | off | status]" % sys.argv[0])
		sys.exit(2)
