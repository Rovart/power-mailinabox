#!/usr/local/lib/mailinabox/env/bin/python
#
# Public IP blocklists (control panel: Mail > Filtering > Blocklists).
#
# Password guessing against the box's logins now comes from thousands of addresses
# that each try a few times, hours apart, which fail2ban can't catch. Most of those
# addresses are already on public lists of attackers. This downloads the lists the
# administrator chose (like an ad blocker's filter lists) and drops connections from
# the addresses on them to the login ports: 465 and 587 (sending mail), 993, 995, 143
# and 110 (IMAP and POP) and 4190 (Sieve). Port 25 is never blocked, so mail from other
# servers always arrives (it has its own checks: Spamhaus, greylisting, SPF...), and
# neither are the web ports.
#
# * The entries of all the lists are merged into one list of networks without
#   duplicates or overlaps: an address that is on several lists, or inside a network
#   that is on a list, is only kept once.
# * A list that can't be downloaded, or that has no valid entries (e.g. an error page),
#   adds nothing: it never blocks anything by itself, and the other lists still apply.
#   The control panel shows the error.
# * Entries can be IP addresses, networks (CIDR) and ranges (a.b.c.d-e.f.g.h), one per
#   line, with comments after #, ; or //. JSON lines with a "cidr" or "ip" key (the
#   format of Spamhaus DROP) are understood too. Private, reserved and other
#   non-public addresses are skipped, and so are networks larger than a /8 (IPv4) or
#   a /16 (IPv6).
# * Never blocked: the box's own addresses, the addresses in fail2ban's ignoreip lists
#   (e.g. the administrator's), and the networks from which the box's users logged in
#   successfully in the last 30 days (see login_alerts.py), so a user whose dynamic
#   address was once used by an attacker isn't locked out.
#
# The rules are a separate nftables table (inet mailinabox_ip_blocklists), next to
# ufw's, written to /etc/mailinabox-ip-blocklists.nft and loaded at boot by the
# mailinabox-ip-blocklists service (created by setup/system.sh, unless the firewall
# is disabled). The lists are downloaded again every 6 hours (cron, see
# setup/management.sh). The settings are in the "ip_blocklists" key of
# $STORAGE_ROOT/settings.yaml; the blocklists are on unless turned off.
########################################################################

import concurrent.futures
import datetime
import glob
import ipaddress
import json
import os
import re
import subprocess
import sys
import urllib.parse
import urllib.request

import utils

DEFAULT_SOURCES = [
	("https://lists.blocklist.de/lists/mail.txt",
	"blocklist.de: addresses reported for attacks on mail servers (SMTP) in the last 48 hours"),
	("https://lists.blocklist.de/lists/imap.txt",
	"blocklist.de: addresses reported for attacks on IMAP, POP3 and SASL logins in the last 48 hours"),
	("https://www.spamhaus.org/drop/drop_v4.json",
	"Spamhaus DROP (IPv4): networks hijacked or run by criminals"),
	("https://www.spamhaus.org/drop/drop_v6.json",
	"Spamhaus DROP (IPv6): networks hijacked or run by criminals"),
]

PORTS = (110, 143, 465, 587, 993, 995, 4190)

STATE_DIR = "/var/lib/mailinabox/ip-blocklists"
STATUS_FILE = STATE_DIR + "/status.json"
MERGED_FILE = STATE_DIR + "/merged.txt"
NFT_FILE = "/etc/mailinabox-ip-blocklists.nft"
SERVICE_FILE = "/etc/systemd/system/mailinabox-ip-blocklists.service"
TABLE = "mailinabox_ip_blocklists"

# Downloads.
TIMEOUT = 20
MAX_BYTES = 64 * 1024 * 1024
USER_AGENT = "Power-Mail-in-a-Box-ip-blocklists/1.0"
# A list with more entries than this is treated as broken.
MAX_ENTRIES_PER_LIST = 500000
MIN_PREFIX = {4: 8, 6: 16}
MAX_SOURCES = 30
# Successful logins from a network in this many days keep it from being blocked.
KNOWN_LOGIN_DAYS = 30


# Settings.

def normalize_url(url):
	url = (url or "").strip()
	parts = urllib.parse.urlsplit(url)
	if parts.scheme not in ("http", "https") or not parts.netloc or len(url) > 2000 or re.search(r"\s", url):
		raise ValueError("%s is not an http:// or https:// address." % (url or "The address"))
	return url


def get_settings(env):
	# Returns {"enabled": bool, "sources": [{"url", "enabled", "description"}]}.
	config = utils.load_settings(env).get("ip_blocklists")
	if not isinstance(config, dict):
		config = {}
	descriptions = dict(DEFAULT_SOURCES)
	sources = []
	if isinstance(config.get("sources"), list):
		for source in config["sources"]:
			if not isinstance(source, dict):
				continue
			try:
				url = normalize_url(str(source.get("url", "")))
			except ValueError:
				continue  # edited by hand
			if any(s["url"] == url for s in sources):
				continue
			sources.append({
				"url": url,
				"enabled": source.get("enabled", True) is not False,
				"description": str(source.get("description") or descriptions.get(url, "")),
			})
	else:
		sources = [{"url": url, "enabled": True, "description": d} for url, d in DEFAULT_SOURCES]
	return {
		"enabled": config.get("enabled", True) is not False,
		"sources": sources,
	}


def save_settings(env, settings):
	config = utils.load_settings(env)
	config["ip_blocklists"] = {
		"enabled": bool(settings["enabled"]),
		"sources": [{
			"url": s["url"],
			"enabled": bool(s["enabled"]),
			"description": s["description"],
		} for s in settings["sources"]],
	}
	utils.write_settings(config, env)


def set_enabled(env, enabled):
	settings = get_settings(env)
	settings["enabled"] = bool(enabled)
	save_settings(env, settings)
	if settings["enabled"]:
		return update(env)
	return apply(env)


def add_source(env, url, description=""):
	url = normalize_url(url)
	settings = get_settings(env)
	if any(s["url"] == url for s in settings["sources"]):
		raise ValueError("%s is already in the list." % url)
	if len(settings["sources"]) >= MAX_SOURCES:
		raise ValueError("There can be at most %d lists." % MAX_SOURCES)
	settings["sources"].append({"url": url, "enabled": True, "description": (description or "").strip()[:200]})
	save_settings(env, settings)
	return update(env)


def remove_source(env, url):
	settings = get_settings(env)
	url = (url or "").strip()
	if not any(s["url"] == url for s in settings["sources"]):
		raise ValueError("%s is not in the list." % url)
	settings["sources"] = [s for s in settings["sources"] if s["url"] != url]
	save_settings(env, settings)
	return update(env)


def set_source_enabled(env, url, enabled):
	settings = get_settings(env)
	url = (url or "").strip()
	for source in settings["sources"]:
		if source["url"] == url:
			source["enabled"] = bool(enabled)
			break
	else:
		raise ValueError("%s is not in the list." % url)
	save_settings(env, settings)
	return update(env)


def reset_sources(env):
	settings = get_settings(env)
	settings["sources"] = [{"url": url, "enabled": True, "description": d} for url, d in DEFAULT_SOURCES]
	save_settings(env, settings)
	return update(env)


# Parsing.

def parse_network(text):
	# An IP address, network or range, or None. Returns a list of networks.
	text = text.strip()
	if not text:
		return []
	if "-" in text and "/" not in text:
		first, _, last = text.partition("-")
		try:
			first, last = ipaddress.ip_address(first.strip()), ipaddress.ip_address(last.strip())
			return list(ipaddress.summarize_address_range(first, last))
		except (ValueError, TypeError):
			return []
	try:
		return [ipaddress.ip_network(text, strict=False)]
	except ValueError:
		return []


def is_blockable(network):
	return network.prefixlen >= MIN_PREFIX[network.version] and network.is_global \
		and not network.is_multicast


def parse_list(text):
	# Returns (networks, number of lines that weren't valid entries).
	networks, skipped = [], 0
	for line in text.splitlines():
		line = line.strip()
		if not line or line[0] in "#;!" or line.startswith("//"):
			continue
		if line.startswith("{"):
			try:
				item = json.loads(line)
			except ValueError:
				skipped += 1
				continue
			if not isinstance(item, dict) or item.get("type") == "metadata":
				continue
			value = item.get("cidr") or item.get("ip") or item.get("network")
			found = parse_network(str(value)) if value else []
		else:
			token = re.split(r"\s+|,|;|#|//", line, maxsplit=1)[0]
			found = parse_network(token)
		found = [n for n in found if is_blockable(n)]
		if not found:
			skipped += 1
		networks.extend(found)
	return networks, skipped


def download(url):
	request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
	with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
		data = response.read(MAX_BYTES + 1)
	if len(data) > MAX_BYTES:
		raise ValueError("the list is larger than %d MB" % (MAX_BYTES // 1024 // 1024))
	return data.decode("utf-8", errors="replace")


def fetch_source(url):
	# Returns (networks, status). A list that fails or has no valid entries gives no
	# networks.
	now = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
	try:
		networks, skipped = parse_list(download(url))
	except Exception as e:
		return [], {"ok": False, "error": "Could not download it: %s" % (getattr(e, "reason", None) or e), "checked": now}
	if not networks:
		return [], {"ok": False, "error": "It has no valid IP addresses or networks, so it isn't used.", "checked": now}
	if len(networks) > MAX_ENTRIES_PER_LIST:
		return [], {"ok": False, "error": "It has more than %d entries, so it isn't used." % MAX_ENTRIES_PER_LIST, "checked": now}
	return networks, {"ok": True, "entries": len(networks), "skipped": skipped, "checked": now}


def merge(networks):
	# One list per IP version, without duplicates or overlapping networks.
	return {
		version: list(ipaddress.collapse_addresses(n for n in networks if n.version == version))
		for version in (4, 6)
	}


# Addresses never to block.

def read_fail2ban_ignoreip():
	networks = []
	files = sorted(glob.glob("/etc/fail2ban/jail.d/*.conf") + glob.glob("/etc/fail2ban/jail.d/*.local"))
	files += [f for f in ("/etc/fail2ban/jail.local",) if os.path.exists(f)]
	for fn in files:
		try:
			text = open(fn, errors="replace").read()
		except OSError:
			continue
		for m in re.finditer(r"^\s*ignoreip\s*=\s*(.*(?:\n[ \t]+.*)*)", text, re.M):
			for token in re.split(r"[\s,]+", re.sub(r"#.*", "", m.group(1))):
				networks.extend(parse_network(token))
	return networks


def get_exempt_networks(env):
	networks = []
	for key in ("PUBLIC_IP", "PUBLIC_IPV6", "PRIVATE_IP", "PRIVATE_IPV6"):
		networks.extend(parse_network(env.get(key) or ""))
	networks.extend(read_fail2ban_ignoreip())
	try:
		import login_alerts
		networks.extend(login_alerts.get_recent_networks(KNOWN_LOGIN_DAYS))
	except Exception:
		pass
	return merge(networks)


# The nftables table.

def is_available():
	# The firewall may have been disabled at setup (DISABLE_FIREWALL=1), e.g. in containers.
	return os.path.exists(SERVICE_FILE) and os.path.exists("/usr/sbin/nft")


def nft_set(name, version, networks):
	lines = ["\tset %s {" % name, "\t\ttype %s" % ("ipv4_addr" if version == 4 else "ipv6_addr"), "\t\tflags interval"]
	if networks:
		lines.append("\t\telements = {")
		lines.extend("\t\t\t%s," % n for n in networks)
		lines.append("\t\t}")
	lines.append("\t}")
	return lines


def nft_rules(block, allow):
	lines = [
		"# Written by Mail-in-a-Box (management/ip_blocklists.py). Changes made here are lost:",
		"# use the control panel (Mail > Filtering) instead.",
		"table inet %s" % TABLE,
		"delete table inet %s" % TABLE,
		"table inet %s {" % TABLE,
	]
	lines += nft_set("allow4", 4, allow[4]) + nft_set("allow6", 6, allow[6])
	lines += nft_set("block4", 4, block[4]) + nft_set("block6", 6, block[6])
	lines += [
		"\tchain logins {",
		"\t\tip saddr @allow4 return",
		"\t\tip6 saddr @allow6 return",
		"\t\tip saddr @block4 counter drop",
		"\t\tip6 saddr @block6 counter drop",
		"\t}",
		"\tchain input {",
		"\t\ttype filter hook input priority -10; policy accept;",
		"\t\tiifname \"lo\" return",
		"\t\ttcp dport { %s } jump logins" % ", ".join(str(p) for p in PORTS),
		"\t}",
		"}",
	]
	return "\n".join(lines) + "\n"


def remove_table():
	subprocess.run(["/usr/sbin/nft", "delete", "table", "inet", TABLE],
				stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
	if os.path.exists(NFT_FILE):
		os.unlink(NFT_FILE)


def load_rules(block, allow):
	# Checks the rules before loading them, so a bad file never replaces the rules in use.
	# Loading the file is a single transaction: the old table is replaced atomically.
	tmp = NFT_FILE + ".tmp"
	with open(tmp, "w") as f:
		f.write(nft_rules(block, allow))
	os.chmod(tmp, 0o600)
	check = subprocess.run(["/usr/sbin/nft", "-c", "-f", tmp], capture_output=True, text=True)
	if check.returncode != 0:
		os.unlink(tmp)
		raise RuntimeError("nftables refused the rules: " + check.stderr.strip()[:500])
	os.replace(tmp, NFT_FILE)
	result = subprocess.run(["/usr/sbin/nft", "-f", NFT_FILE], capture_output=True, text=True)
	if result.returncode != 0:
		raise RuntimeError("Could not load the rules: " + result.stderr.strip()[:500])


def read_status():
	try:
		with open(STATUS_FILE) as f:
			status = json.load(f)
			return status if isinstance(status, dict) else {}
	except (OSError, ValueError):
		return {}


def write_status(status):
	os.makedirs(STATE_DIR, exist_ok=True)
	with open(STATUS_FILE + ".tmp", "w") as f:
		json.dump(status, f, indent=1)
	os.replace(STATUS_FILE + ".tmp", STATUS_FILE)


def read_merged():
	networks = []
	try:
		with open(MERGED_FILE) as f:
			for line in f:
				networks.extend(parse_network(line))
	except OSError:
		pass
	return merge(networks)


def update(env):
	# Downloads the enabled lists, merges them and loads the rules. Returns a description.
	settings = get_settings(env)
	if not settings["enabled"] or not is_available():
		return apply(env)
	sources = [s for s in settings["sources"] if s["enabled"]]
	results = {}
	with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
		futures = {pool.submit(fetch_source, s["url"]): s["url"] for s in sources}
		for future in concurrent.futures.as_completed(futures):
			results[futures[future]] = future.result()
	networks = []
	for networks_of_source, _ in results.values():
		networks.extend(networks_of_source)
	block = merge(networks)
	os.makedirs(STATE_DIR, exist_ok=True)
	with open(MERGED_FILE + ".tmp", "w") as f:
		for version in (4, 6):
			f.writelines("%s\n" % n for n in block[version])
	os.replace(MERGED_FILE + ".tmp", MERGED_FILE)
	status = {
		"updated": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
		"sources": {url: s for url, (_, s) in results.items()},
	}
	write_status(status)
	return apply(env, block)


def apply(env, block=None):
	# Loads the rules from the last merged lists (without downloading them), e.g. after
	# the networks of known logins changed, or removes them if the blocklists are off.
	settings = get_settings(env)
	if not is_available():
		return "The blocklists are not available because the firewall was disabled at setup.\n"
	if not settings["enabled"]:
		remove_table()
		return "The blocklists are off.\n"
	if block is None:
		block = read_merged()
	allow = get_exempt_networks(env)
	load_rules(block, allow)
	failed = [url for url, s in read_status().get("sources", {}).items() if not s.get("ok")]
	return "Blocking %d IPv4 and %d IPv6 networks on the login ports%s.\n" % (
		len(block[4]), len(block[6]),
		" (%d of the lists failed and add nothing: %s)" % (len(failed), ", ".join(failed)) if failed else "")


def get_dropped():
	# Packets dropped since the rules were last loaded.
	try:
		out = subprocess.run(["/usr/sbin/nft", "list", "chain", "inet", TABLE, "logins"],
							capture_output=True, text=True, timeout=10).stdout
	except (OSError, subprocess.SubprocessError):
		return None
	counts = [int(n) for n in re.findall(r"counter packets (\d+)", out)]
	return sum(counts) if counts else None


def get_status(env):
	settings = get_settings(env)
	state = read_status()
	block = read_merged() if settings["enabled"] else {4: [], 6: []}
	sources = []
	for source in settings["sources"]:
		item = dict(source)
		item.update(state.get("sources", {}).get(source["url"], {}) if source["enabled"] else {})
		sources.append(item)
	return {
		"available": is_available(),
		"enabled": settings["enabled"],
		"updated": state.get("updated"),
		"networks4": len(block[4]),
		"networks6": len(block[6]),
		"addresses4": sum(n.num_addresses for n in block[4]),
		"dropped": get_dropped() if settings["enabled"] and is_available() else None,
		"ports": list(PORTS),
		"sources": sources,
		"defaults": [url for url, _ in DEFAULT_SOURCES],
	}


if __name__ == "__main__":
	env = utils.load_environment()
	commands = {
		"update": lambda: update(env),
		"apply": lambda: apply(env),
		"on": lambda: set_enabled(env, True),
		"off": lambda: set_enabled(env, False),
	}
	if len(sys.argv) == 2 and sys.argv[1] in commands:
		try:
			print(commands[sys.argv[1]](), end="")
		except (RuntimeError, ValueError) as e:
			print(e, file=sys.stderr)
			sys.exit(1)
	elif len(sys.argv) == 2 and sys.argv[1] == "status":
		print(json.dumps(get_status(env), indent=1))
	elif len(sys.argv) == 3 and sys.argv[1] in ("add", "remove"):
		try:
			print((add_source if sys.argv[1] == "add" else remove_source)(env, sys.argv[2]), end="")
		except (RuntimeError, ValueError) as e:
			print(e, file=sys.stderr)
			sys.exit(1)
	else:
		print("Usage: %s update | apply | on | off | status | add URL | remove URL" % sys.argv[0])
		sys.exit(2)
