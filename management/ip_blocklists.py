#!/usr/local/lib/mailinabox/env/bin/python
#
# Public IP blocklists (control panel: Mail > Filtering > Blocklists).
#
# Password guessing against the box's logins now comes from thousands of addresses
# that each try a few times, hours apart, which fail2ban can't catch. Most of those
# addresses are already on public lists of attackers. This downloads the lists the
# administrator chose (like an ad blocker's filter lists) and drops connections from
# the addresses on them. Each list has a scope:
#
# * "logins" (the default): only the login ports, 465 and 587 (sending mail), 993, 995,
#   143 and 110 (IMAP and POP) and 4190 (Sieve). Port 25 stays open, so mail from other
#   servers always arrives (it has its own checks: Spamhaus, greylisting, SPF...), and
#   so do the web ports. Right for lists of reported addresses, which may be reassigned.
# * "all": every port except DNS (53), so the box's domains always resolve, even for a
#   resolver that ends up on a list. Right for lists of networks that never have a
#   legitimate reason to connect, like Spamhaus DROP.
#
# Besides the public lists, the administrator keeps a list of their own (the "manual"
# key, e.g. from the Block buttons of the dashboard), always blocked on every port but
# DNS, also when the public lists are turned off. Replies to connections the box itself
# opened are never dropped.
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
# $STORAGE_ROOT/settings.yaml; the blocklists are on unless turned off. The country
# filter (geofence.py) is part of the same table, in its own chain.
########################################################################

import bisect
import concurrent.futures
import datetime
import glob
import hashlib
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

# The scope of the default lists that differ from "logins".
DEFAULT_SCOPES = {
	"https://www.spamhaus.org/drop/drop_v4.json": "all",
	"https://www.spamhaus.org/drop/drop_v6.json": "all",
}

# Lists the control panel suggests: (address, description, scope). Sizes in late 2026.
SUGGESTED_SOURCES = [
	("https://raw.githubusercontent.com/firehol/blocklist-ipsets/master/firehol_level1.netset",
	"FireHOL level 1: networks that should never connect (Spamhaus DROP, DShield, Feodo...), about 4,600 entries", "all"),
	("https://raw.githubusercontent.com/firehol/blocklist-ipsets/master/dshield.netset",
	"DShield: the 20 networks (/24) attacking the most right now", "all"),
	("https://raw.githubusercontent.com/stamparm/ipsum/master/levels/3.txt",
	"IPsum level 3: addresses on 3 or more blacklists, updated daily, about 17,000 entries", "logins"),
	("https://blocklist.greensnow.co/greensnow.txt",
	"GreenSnow: brute force attacks (SSH, FTP, mail) seen by its sensors, about 3,300 entries", "logins"),
	("https://cinsscore.com/list/ci-badguys.txt",
	"CINS Army: addresses with a poor score on its sensor network, 15,000 entries", "logins"),
	("https://lists.blocklist.de/lists/all.txt",
	"blocklist.de, all services (SSH, web, mail...), about 24,000 entries", "logins"),
	("https://rules.emergingthreats.net/blockrules/compromised-ips.txt",
	"Emerging Threats: known compromised hosts, about 500 entries", "all"),
]

SCOPES = ("logins", "all")
DNS_PORT = 53
MAX_MANUAL = 5000

PORTS = (110, 143, 465, 587, 993, 995, 4190)

STATE_DIR = "/var/lib/mailinabox/ip-blocklists"
STATUS_FILE = STATE_DIR + "/status.json"
MERGED_FILE = STATE_DIR + "/merged.txt"  # scope "logins"
MERGED_ALL_FILE = STATE_DIR + "/merged-all.txt"  # scope "all"
# The last good download of each list, so that adding, removing or changing one list
# doesn't download all the others again (they are every 6 hours, or with "Download the
# lists now").
CACHE_DIR = STATE_DIR + "/lists"
NFT_FILE = "/etc/mailinabox-ip-blocklists.nft"
SERVICE_FILE = "/etc/systemd/system/mailinabox-ip-blocklists.service"
TABLE = "mailinabox_ip_blocklists"

# Downloads.
TIMEOUT = 20
MAX_BYTES = 64 * 1024 * 1024
USER_AGENT = "Enhanced-Mail-in-a-Box-ip-blocklists/1.0"
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
			scope = source.get("scope")
			sources.append({
				"url": url,
				"enabled": source.get("enabled", True) is not False,
				"description": str(source.get("description") or descriptions.get(url, "")),
				"scope": scope if scope in SCOPES else default_scope(url),
			})
	else:
		sources = default_sources()
	manual = []
	for item in config.get("manual") or []:
		if not isinstance(item, dict):
			continue
		found = parse_network(str(item.get("entry", "")))
		if len(found) != 1:
			continue  # edited by hand
		entry = canonical(found[0])
		if any(m["entry"] == entry for m in manual):
			continue
		manual.append({
			"entry": entry,
			"note": str(item.get("note") or "")[:200],
			"added": str(item.get("added") or ""),
		})
	return {
		"enabled": config.get("enabled", True) is not False,
		"sources": sources,
		"manual": manual,
	}


def default_scope(url):
	if url in DEFAULT_SCOPES:
		return DEFAULT_SCOPES[url]
	for suggested, _, scope in SUGGESTED_SOURCES:
		if suggested == url:
			return scope
	return "logins"


def default_sources():
	return [{"url": url, "enabled": True, "description": d, "scope": default_scope(url)}
			for url, d in DEFAULT_SOURCES]


def save_settings(env, settings):
	config = utils.load_settings(env)
	config["ip_blocklists"] = {
		"enabled": bool(settings["enabled"]),
		"sources": [{
			"url": s["url"],
			"enabled": bool(s["enabled"]),
			"description": s["description"],
			"scope": s["scope"],
		} for s in settings["sources"]],
		"manual": settings.get("manual", []),
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
	if not description:
		description = dict((u, d) for u, d, _ in SUGGESTED_SOURCES).get(url, "")
	settings["sources"].append({"url": url, "enabled": True, "description": (description or "").strip()[:200],
								"scope": default_scope(url)})
	save_settings(env, settings)
	return update(env, download=False)


def remove_source(env, url):
	settings = get_settings(env)
	url = (url or "").strip()
	if not any(s["url"] == url for s in settings["sources"]):
		raise ValueError("%s is not in the list." % url)
	settings["sources"] = [s for s in settings["sources"] if s["url"] != url]
	save_settings(env, settings)
	return update(env, download=False)


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
	return update(env, download=False)


def set_source_scope(env, url, scope):
	if scope not in SCOPES:
		raise ValueError("Unknown scope: %s" % scope)
	settings = get_settings(env)
	url = (url or "").strip()
	for source in settings["sources"]:
		if source["url"] == url:
			source["scope"] = scope
			break
	else:
		raise ValueError("%s is not in the list." % url)
	save_settings(env, settings)
	return update(env, download=False)


def reset_sources(env):
	settings = get_settings(env)
	settings["sources"] = default_sources()
	save_settings(env, settings)
	return update(env, download=False)


# The administrator's own list.

def canonical(network):
	# "45.139.104.172" for a single address, "45.139.104.0/24" for a network.
	return str(network.network_address) if network.num_addresses == 1 else str(network)

def add_manual(env, entry, note=""):
	# Blocks an address or network on every port but DNS. Returns a description.
	found = parse_network((entry or "").strip()) if "-" not in (entry or "") else []
	if len(found) != 1:
		raise ValueError("%s is not an IP address or network." % ((entry or "").strip() or "The entry"))
	network = found[0]
	if not network.is_global or network.is_multicast:
		raise ValueError("%s is not a public address: it can't come from the internet." % canonical(network))
	if network.prefixlen < MIN_PREFIX[network.version]:
		raise ValueError("%s is too large a network." % canonical(network))
	for exempt in get_exempt_networks(env)[network.version]:
		if network.overlaps(exempt):
			raise ValueError(
				"%s can't be blocked: it overlaps %s, which is never blocked (the box's own address, "
				"an address in fail2ban's ignoreip, or a network your users logged in from in the last %d days)."
				% (canonical(network), exempt, KNOWN_LOGIN_DAYS))
	settings = get_settings(env)
	if any(m["entry"] == canonical(network) for m in settings["manual"]):
		raise ValueError("%s is already blocked." % canonical(network))
	if len(settings["manual"]) >= MAX_MANUAL:
		raise ValueError("Your list can have at most %d entries." % MAX_MANUAL)
	settings["manual"].append({
		"entry": canonical(network),
		"note": (note or "").strip()[:200],
		"added": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
	})
	save_settings(env, settings)
	apply(env)
	return "%s is blocked on every port but DNS.\n" % canonical(network)


def remove_manual(env, entry):
	settings = get_settings(env)
	found = parse_network((entry or "").strip())
	key = canonical(found[0]) if len(found) == 1 else (entry or "").strip()
	if not any(m["entry"] == key for m in settings["manual"]):
		raise ValueError("%s is not in your list." % key)
	settings["manual"] = [m for m in settings["manual"] if m["entry"] != key]
	save_settings(env, settings)
	apply(env)
	return "%s is no longer blocked.\n" % key


def manual_networks(settings):
	networks = []
	for item in settings.get("manual", []):
		networks.extend(parse_network(item["entry"]))
	return merge(networks)


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
		raise ValueError("the list is larger than %d MB. Lists that big have millions of entries, which would "
						"take more memory than the box can spare (the kernel keeps every entry in memory); "
						"use a smaller, curated list instead (see the suggested lists)" % (MAX_BYTES // 1024 // 1024))
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
		return [], {"ok": False, "error": "It has more than %d entries, which would take more memory than the box can spare, so it isn't used. Use a smaller, curated list instead." % MAX_ENTRIES_PER_LIST, "checked": now}
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


def nft_rules(block, allow, block_all=None, geo=None):
	# block: networks blocked on the login ports; block_all: on every port but DNS;
	# geo: the country filter's rules (see geofence.py), or None.
	import geofence
	block_all = block_all or {4: [], 6: []}
	lines = [
		"# Written by Mail-in-a-Box (management/ip_blocklists.py). Changes made here are lost:",
		"# use the control panel (Mail > Filtering) instead.",
		"table inet %s" % TABLE,
		"delete table inet %s" % TABLE,
		"table inet %s {" % TABLE,
	]
	lines += nft_set("allow4", 4, allow[4]) + nft_set("allow6", 6, allow[6])
	lines += nft_set("block4", 4, block[4]) + nft_set("block6", 6, block[6])
	lines += nft_set("all4", 4, block_all[4]) + nft_set("all6", 6, block_all[6])
	if geo:
		lines += nft_set("geo4", 4, geo[4]) + nft_set("geo6", 6, geo[6])
		lines += geofence.nft_chain(geo)
	lines += [
		"\tchain logins {",
		"\t\tip saddr @block4 counter drop",
		"\t\tip6 saddr @block6 counter drop",
		"\t}",
		"\tchain input {",
		"\t\ttype filter hook input priority -10; policy accept;",
		"\t\tiifname \"lo\" return",
		# Replies to connections the box opened (e.g. delivering mail) are never dropped.
		"\t\tct state established,related return",
		"\t\tip saddr @allow4 return",
		"\t\tip6 saddr @allow6 return",
		"\t\ttcp dport %d return" % DNS_PORT,
		"\t\tudp dport %d return" % DNS_PORT,
		"\t\tip saddr @all4 counter drop",
		"\t\tip6 saddr @all6 counter drop",
	]
	if geo:
		lines.append("\t\ttcp dport { %s } jump geofence" % ", ".join(str(p) for p in geo["ports"]))
	lines += [
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


def load_rules(block, allow, block_all=None, geo=None):
	# Loading the file is a single transaction: the old table is replaced atomically, and a
	# file nftables refuses changes nothing. Only rules that loaded become the file loaded
	# at boot, so a bad file never replaces the rules in use. (One pass of nftables instead
	# of a check and then a load: the file has every network of the lists.)
	tmp = NFT_FILE + ".tmp"
	with open(tmp, "w") as f:
		f.write(nft_rules(block, allow, block_all, geo))
	os.chmod(tmp, 0o600)
	result = subprocess.run(["/usr/sbin/nft", "-f", tmp], capture_output=True, text=True)
	if result.returncode != 0:
		os.unlink(tmp)
		raise RuntimeError("nftables refused the rules: " + result.stderr.strip()[:500])
	os.replace(tmp, NFT_FILE)


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


def read_merged_file(fn):
	# The merged files are written by write_merged(): one network per line, already merged
	# and sorted, so they are read as they are. (Parsing every line as a range and merging
	# again took about a second on a small box, on every change in the control panel.)
	networks = {4: [], 6: []}
	try:
		with open(fn) as f:
			for line in f:
				line = line.strip()
				if not line:
					continue
				try:
					found = [ipaddress.ip_network(line)]
				except ValueError:
					found = parse_network(line)
				for n in found:
					networks[n.version].append(n)
	except OSError:
		pass
	return networks


def read_merged():
	# Every network on the public lists, whatever its scope.
	logins, every = read_merged_file(MERGED_FILE), read_merged_file(MERGED_ALL_FILE)
	return merge(logins[4] + logins[6] + every[4] + every[6])


def write_merged(fn, networks):
	with open(fn + ".tmp", "w") as f:
		for version in (4, 6):
			f.writelines("%s\n" % n for n in networks[version])
	os.replace(fn + ".tmp", fn)


def cache_file(url):
	return os.path.join(CACHE_DIR, hashlib.sha256(url.encode()).hexdigest()[:32] + ".txt")


def write_cache(url, networks):
	os.makedirs(CACHE_DIR, exist_ok=True)
	fn = cache_file(url)
	with open(fn + ".tmp", "w") as f:
		f.writelines("%s\n" % n for n in networks)
	os.replace(fn + ".tmp", fn)


def read_cache(url):
	# The networks of the list's last good download, or None.
	networks = []
	try:
		with open(cache_file(url)) as f:
			for line in f:
				try:
					networks.append(ipaddress.ip_network(line.strip()))
				except ValueError:
					pass
	except OSError:
		return None
	return networks


def prune_cache(urls):
	# Removes the downloads of the lists that are no longer in the settings.
	keep = {os.path.basename(cache_file(u)) for u in urls}
	for fn in glob.glob(os.path.join(CACHE_DIR, "*.txt")):
		if os.path.basename(fn) not in keep:
			try:
				os.unlink(fn)
			except OSError:
				pass


def fetch_and_cache(url):
	networks, status = fetch_source(url)
	if status.get("ok"):
		write_cache(url, networks)
	return networks, status


def update(env, download=True):
	# Merges the enabled lists and loads the rules. Returns a description.
	# download=True downloads every list again (every 6 hours, "Download the lists now",
	# turning the lists on); download=False only downloads the lists without a good
	# download yet (e.g. one just added) and takes the others from their last download,
	# so that a change in the control panel answers in seconds.
	settings = get_settings(env)
	if download and is_available():
		# The country filter's data, at most once a day (see geofence.py). A failed download
		# keeps the previous data, and the control panel shows the error.
		import geofence
		if geofence.get_settings(env)["enabled"]:
			try:
				geofence.update(env)
			except RuntimeError:
				pass
	if not settings["enabled"] or not is_available():
		return apply(env)
	sources = [s for s in settings["sources"] if s["enabled"]]
	scopes = {s["url"]: s["scope"] for s in sources}
	results = {}
	to_fetch = []
	previous = read_status().get("sources", {})
	for s in sources:
		cached = None if download else read_cache(s["url"])
		if cached is not None and previous.get(s["url"], {}).get("ok"):
			results[s["url"]] = (cached, previous[s["url"]])
		else:
			to_fetch.append(s["url"])
	if to_fetch:
		with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
			futures = {pool.submit(fetch_and_cache, url): url for url in to_fetch}
			for future in concurrent.futures.as_completed(futures):
				results[futures[future]] = future.result()
	prune_cache([s["url"] for s in settings["sources"]])
	networks = {"logins": [], "all": []}
	for url, (networks_of_source, _) in results.items():
		networks[scopes.get(url, "logins")].extend(networks_of_source)
	block_all = merge(networks["all"])
	# What is blocked on every port doesn't need to be in the login ports' list too.
	block = merge(networks["logins"])
	block = subtract(block, block_all)
	os.makedirs(STATE_DIR, exist_ok=True)
	write_merged(MERGED_FILE, block)
	write_merged(MERGED_ALL_FILE, block_all)
	status = {
		"updated": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
		"sources": {url: s for url, (_, s) in results.items()},
	}
	write_status(status)
	return apply(env, block, block_all)


def subtract(networks, minus):
	# The networks without the parts that are in minus (both merged). minus becomes
	# sorted, non-overlapping intervals of integers, and each network only looks at the
	# intervals that can overlap it (a binary search), so this takes a fraction of a
	# second for tens of thousands of networks. (Comparing every network with every
	# network of minus took minutes on a small box, longer than the control panel waits.)
	result = {}
	for version in (4, 6):
		address = ipaddress.IPv4Address if version == 4 else ipaddress.IPv6Address
		cuts = []
		for m in sorted(minus[version]):
			start, end = int(m.network_address), int(m.broadcast_address)
			if cuts and start <= cuts[-1][1] + 1:
				cuts[-1][1] = max(cuts[-1][1], end)
			else:
				cuts.append([start, end])
		starts = [c[0] for c in cuts]
		remaining = []
		for n in networks[version]:
			start, end = int(n.network_address), int(n.broadcast_address)
			first = max(bisect.bisect_right(starts, start) - 1, 0)
			last = bisect.bisect_right(starts, end)
			pieces, position = [], start
			for cut_start, cut_end in cuts[first:last]:
				if cut_end < position:
					continue
				if cut_start > position:
					pieces.append((position, cut_start - 1))
				position = cut_end + 1
				if position > end:
					break
			if position <= end:
				pieces.append((position, end))
			if pieces == [(start, end)]:
				remaining.append(n)
			else:
				for piece_start, piece_end in pieces:
					remaining.extend(ipaddress.summarize_address_range(address(piece_start), address(piece_end)))
		result[version] = list(ipaddress.collapse_addresses(remaining))
	return result


def apply(env, block=None, block_all=None):
	# Loads the rules from the last merged lists (without downloading them), e.g. after
	# the networks of known logins or the administrator's own list changed, or removes
	# them if the blocklists are off and the administrator's list is empty.
	import geofence
	settings = get_settings(env)
	if not is_available():
		return "The blocklists are not available because the firewall was disabled at setup.\n"
	manual = manual_networks(settings)
	geo, geo_reason = geofence.get_rules(env)
	if not settings["enabled"]:
		block, block_all = {4: [], 6: []}, {4: [], 6: []}
		if not manual[4] and not manual[6] and not geo:
			remove_table()
			return "The blocklists are off.\n" + (geo_reason + "\n" if geofence.get_settings(env)["enabled"] else "")
	else:
		if block is None:
			block = read_merged_file(MERGED_FILE)
		if block_all is None:
			block_all = read_merged_file(MERGED_ALL_FILE)
	every = merge(block_all[4] + block_all[6] + manual[4] + manual[6])
	allow = get_exempt_networks(env)
	load_rules(block, allow, every, geo)
	failed = [url for url, s in read_status().get("sources", {}).items() if not s.get("ok")] if settings["enabled"] else []
	if geo:
		countries = ", ".join(geofence.get_settings(env)["countries"])
		geo_text = "%s connections from %s on ports %s.\n" % (
			"Allowing only" if geo["mode"] == "allow" else "Blocking", countries,
			", ".join(str(p) for p in geo["ports"]))
	else:
		geo_text = geo_reason + "\n" if geofence.get_settings(env)["enabled"] else ""
	return "Blocking %d networks on the login ports and %d on every port (%d of them yours)%s.\n%s" % (
		len(block[4]) + len(block[6]), len(every[4]) + len(every[6]), len(settings["manual"]),
		" (%d of the lists failed and add nothing: %s)" % (len(failed), ", ".join(failed)) if failed else "",
		geo_text)


def get_dropped():
	# Packets dropped since the rules were last loaded.
	try:
		out = subprocess.run(["/usr/sbin/nft", "list", "table", "inet", TABLE],
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
	every = read_merged_file(MERGED_ALL_FILE) if settings["enabled"] else {4: [], 6: []}
	added = {s["url"] for s in settings["sources"]}
	return {
		"available": is_available(),
		"enabled": settings["enabled"],
		"updated": state.get("updated"),
		"networks4": len(block[4]),
		"networks6": len(block[6]),
		"networks_all": len(every[4]) + len(every[6]),
		"addresses4": sum(n.num_addresses for n in block[4]),
		"dropped": get_dropped() if is_available() else None,
		"ports": list(PORTS),
		"sources": sources,
		"defaults": [url for url, _ in DEFAULT_SOURCES],
		"suggested": [{"url": u, "description": d, "scope": sc} for u, d, sc in SUGGESTED_SOURCES if u not in added],
		"manual": sorted(settings["manual"], key=lambda m: m.get("added", ""), reverse=True),
	}


# CDNs and reverse proxies: sites behind one only see its addresses, so the firewall
# can't block their attackers. The control panel offers the blocked addresses as a file
# to upload to the IP list (or IP set, ACL, network list) of any CDN or WAF, used by a
# rule that blocks the requests coming from it. Lists differ in what they accept, so the
# format, the number of entries and the IPv6 entries can be chosen.

EXPORT_FORMATS = ("txt", "csv")
EXPORT_IPV6 = ("all", "single", "none")
EXPORT_DEFAULT_LIMIT = 10000
EXPORT_MAX_LIMIT = 1000000
WEB_JAIL_WORDS = ("web", "nginx", "http", "apache", "roundcube", "owncloud", "nextcloud", "munin", "management", "php")


def export_items(env, limit=EXPORT_DEFAULT_LIMIT, ipv6="all"):
	# [(address, description)], most useful first: your own list, the addresses fail2ban
	# bans now in its web jails, the lists that block every port, then the others.
	# ipv6: "all" (addresses and networks), "single" (only single addresses) or "none".
	# Networks broader than an IPv4 /8 or an IPv6 /16 are left out: no list should hold them.
	items, seen = [], set()

	def add(network, description):
		if len(items) >= limit:
			return
		if network.version == 6 and (ipv6 == "none" or (ipv6 == "single" and network.num_addresses != 1)):
			return
		if network.prefixlen < (8 if network.version == 4 else 16):
			return
		key = canonical(network)
		if key in seen:
			return
		seen.add(key)
		items.append((key, description[:500]))

	settings = get_settings(env)
	for m in settings["manual"]:
		for n in parse_network(m["entry"]):
			add(n, "Blocked by you" + (": " + m["note"] if m["note"] else ""))
	try:
		import security_metrics
		for address, jails in sorted(security_metrics.banned_now().items()):
			web = [j for j in jails if any(w in j.lower() for w in WEB_JAIL_WORDS)]
			if web:
				for n in parse_network(address):
					add(n, "Banned by fail2ban (%s)" % ", ".join(web))
	except Exception:
		pass
	if settings["enabled"]:
		for fn, description in ((MERGED_ALL_FILE, "Public lists (every port)"), (MERGED_FILE, "Public lists (login ports)")):
			merged = read_merged_file(fn)
			for n in merged[4] + merged[6]:
				add(n, description)
	return items


def export_blocked(env, fmt="txt", limit=EXPORT_DEFAULT_LIMIT, ipv6="all"):
	# "txt": one address or network per line, the format almost every list accepts.
	# "csv": address,description, for lists that keep a note with each entry.
	items = export_items(env, limit, ipv6)
	if fmt == "txt":
		return "".join(address + "\n" for address, _ in items)
	import csv
	import io
	out = io.StringIO()
	writer = csv.writer(out, lineterminator="\n")
	for address, description in items:
		writer.writerow([address, description])
	return out.getvalue()


def manual_matcher(env):
	# A function telling whether an address is on the administrator's own list.
	networks = manual_networks(get_settings(env))

	def matches(address):
		try:
			ip = ipaddress.ip_address(address)
		except ValueError:
			return None
		for n in networks[ip.version]:
			if ip in n:
				return str(n)
		return None
	return matches


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
	elif len(sys.argv) >= 3 and sys.argv[1] in ("block", "unblock"):
		try:
			if sys.argv[1] == "block":
				print(add_manual(env, sys.argv[2], " ".join(sys.argv[3:])), end="")
			else:
				print(remove_manual(env, sys.argv[2]), end="")
		except (RuntimeError, ValueError) as e:
			print(e, file=sys.stderr)
			sys.exit(1)
	elif len(sys.argv) == 3 and sys.argv[1] in ("add", "remove"):
		try:
			print((add_source if sys.argv[1] == "add" else remove_source)(env, sys.argv[2]), end="")
		except (RuntimeError, ValueError) as e:
			print(e, file=sys.stderr)
			sys.exit(1)
	else:
		print("Usage: %s update | apply | on | off | status | add URL | remove URL | block ADDRESS [NOTE] | unblock ADDRESS" % sys.argv[0])
		sys.exit(2)
