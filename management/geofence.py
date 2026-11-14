#!/usr/local/lib/mailinabox/env/bin/python
#
# Country filter ("geo-fence") for the box's logins (control panel: Mail > Filtering).
#
# A box whose users all live in one country gets most of its password guessing from
# everywhere else. The administrator chooses some countries and either allows only the
# connections from them ("allow") or blocks the connections from them ("block"). It
# applies to the login ports (465 and 587 to send mail, 993, 995, 143 and 110 for IMAP and
# POP, 4190 for Sieve), and optionally to HTTPS (443: the webmail, Nextcloud, Exchange
# ActiveSync and the control panel). Never to port 25: mail from other servers comes from
# anywhere and always arrives. Nor to port 80, which Let's Encrypt needs, nor to DNS.
#
# Never blocked by country, whatever the setting:
# * Addresses that aren't public internet addresses: the local network, Tailscale and
#   other CGNAT addresses (100.64.0.0/10), private IPv6 (fc00::/7, which includes
#   Tailscale's fd7a:115c:a1e0::/48), link-local, loopback. They can't come from another
#   country, and VPNs like Tailscale or WireGuard keep working from anywhere.
# * The same addresses the IP blocklists never block (see ip_blocklists.py): the box's
#   own addresses, fail2ban's ignoreip, and the networks the box's users logged in from
#   in the last 30 days, so turning the filter on doesn't lock out a user who is abroad
#   right now.
# * Replies to connections the box itself opened.
#
# Where an address is: the country in which its block of addresses is registered, from
# the daily statistics of the five regional internet registries (ARIN, RIPE NCC, APNIC,
# LACNIC and AFRINIC; public, no account or key). It's where the network's holder is,
# which for the internet providers of a country is that country: an operator's mobile
# customers abroad usually keep its addresses (roaming goes through the home network).
# Large hosting and cloud providers may use addresses registered elsewhere.
#
# The registries' files are downloaded at most once a day (the IP blocklists' 6-hourly
# run calls update()), only while the filter is on. They are downloaded together: if one
# fails, the previous data stays in use, since a country's addresses can be registered
# with several registries. The filter fails open: while there is no data for a chosen
# country (or no country is chosen), nothing is blocked by country, and the control panel
# says why.
#
# The rules are part of the IP blocklists' nftables table (inet mailinabox_ip_blocklists,
# see ip_blocklists.py), loaded at boot by its service. The settings are in the
# "geofence" key of $STORAGE_ROOT/settings.yaml; the filter is off unless turned on. The
# "sources" key there can list other addresses for the registries' files (a mirror).
########################################################################

import concurrent.futures
import datetime
import glob
import ipaddress
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.request

import utils

DEFAULT_SOURCES = [
	"https://ftp.arin.net/pub/stats/arin/delegated-arin-extended-latest",
	"https://ftp.ripe.net/pub/stats/ripencc/delegated-ripencc-extended-latest",
	"https://ftp.apnic.net/stats/apnic/delegated-apnic-extended-latest",
	"https://ftp.lacnic.net/pub/stats/lacnic/delegated-lacnic-extended-latest",
	"https://ftp.afrinic.net/pub/stats/afrinic/delegated-afrinic-extended-latest",
]

MODES = ("allow", "block")
SCOPES = ("logins", "web")
WEB_PORTS = (443,)

# Never filtered by country (see above).
NOT_PUBLIC = {
	4: ["0.0.0.0/8", "10.0.0.0/8", "100.64.0.0/10", "127.0.0.0/8", "169.254.0.0/16", "172.16.0.0/12",
		"192.168.0.0/16"],
	6: ["::1/128", "fc00::/7", "fe80::/10"],
}

STATE_DIR = "/var/lib/mailinabox/geofence"
STATUS_FILE = STATE_DIR + "/status.json"
COUNTRIES_DIR = STATE_DIR + "/countries"  # XX.txt: the merged networks of each country
COUNTRY_NAMES_FILE = os.path.join(os.path.dirname(os.path.realpath(__file__)), "csr_country_codes.tsv")
# Codes the registries use that aren't countries.
EXTRA_NAMES = {"EU": "European Union (not a single country)", "AP": "Asia-Pacific (not a single country)"}

# Downloads.
TIMEOUT = 60
MAX_BYTES = 64 * 1024 * 1024
USER_AGENT = "Enhanced-Mail-in-a-Box-geofence/1.0"
MAX_AGE_HOURS = 20  # the registries publish once a day
MAX_SOURCES = 10


# Settings.

def get_settings(env):
	# {"enabled", "mode", "scope", "countries": ["ES", ...], "sources": [url, ...]}
	config = utils.load_settings(env).get("geofence")
	if not isinstance(config, dict):
		config = {}
	countries = []
	for code in config.get("countries") or []:
		code = str(code).strip().upper()
		if re.fullmatch(r"[A-Z]{2}", code) and code not in countries:
			countries.append(code)
	sources = [str(s).strip() for s in config.get("sources") or [] if re.match(r"^https?://\S+$", str(s).strip())]
	return {
		"enabled": config.get("enabled", False) is True,
		"mode": config.get("mode") if config.get("mode") in MODES else "allow",
		"scope": config.get("scope") if config.get("scope") in SCOPES else "logins",
		"countries": sorted(countries),
		"sources": sources[:MAX_SOURCES] or list(DEFAULT_SOURCES),
		"custom_sources": bool(sources),
	}


def save_settings(env, settings):
	config = utils.load_settings(env)
	saved = {
		"enabled": bool(settings["enabled"]),
		"mode": settings["mode"],
		"scope": settings["scope"],
		"countries": sorted(settings["countries"]),
	}
	if settings.get("custom_sources"):
		saved["sources"] = settings["sources"]
	config["geofence"] = saved
	utils.write_settings(config, env)


def country_names():
	names = dict(EXTRA_NAMES)
	try:
		with open(COUNTRY_NAMES_FILE) as f:
			for line in f:
				if line.strip() and not line.startswith("#"):
					code, name = line.rstrip("\n").split("\t")[0:2]
					names[code.upper()] = name
	except OSError:
		pass
	return names


def known_countries():
	# The codes the registries' data has (the countries that can be chosen), or the ISO
	# codes before the first download.
	codes = sorted(os.path.basename(fn)[:2] for fn in glob.glob(os.path.join(COUNTRIES_DIR, "??.txt")))
	return codes or sorted(c for c in country_names() if c not in EXTRA_NAMES)


def check_country(code):
	code = (code or "").strip().upper()
	if not re.fullmatch(r"[A-Z]{2}", code):
		raise ValueError("%s is not a two-letter country code." % ((code or "").strip() or "The entry"))
	if code not in known_countries():
		raise ValueError("The registries have no addresses for %s." % code)
	return code


# The registries' data.

def parse_registry(text, ranges):
	# Adds the allocations of a registry's "delegated" statistics file to ranges:
	# {country: ([(first, last), ...] for IPv4, [...] for IPv6)}, as integers. Lines are
	# registry|cc|type|start|value|date|status[|...]; the value is the number of addresses
	# (IPv4) or the prefix length (IPv6). Returns the number of allocations.
	count = 0
	for line in text.splitlines():
		f = line.split("|")
		if len(f) < 7 or f[2] not in ("ipv4", "ipv6") or not re.fullmatch(r"[A-Za-z]{2}", f[1]) \
				or f[6].strip() not in ("allocated", "assigned"):
			continue
		try:
			if f[2] == "ipv4":
				first = int(ipaddress.IPv4Address(f[3]))
				last = first + int(f[4]) - 1
				if last > 2**32 - 1 or last < first:
					continue
				version = 0
			else:
				first = int(ipaddress.IPv6Address(f[3]))
				prefix = int(f[4])
				if not 0 < prefix <= 128:
					continue
				last = first + (1 << (128 - prefix)) - 1
				version = 1
		except ValueError:
			continue
		ranges.setdefault(f[1].upper(), ([], []))[version].append((first, last))
		count += 1
	return count


def merge_ranges(ranges):
	# Sorted, non-overlapping, non-adjacent (first, last) ranges.
	merged = []
	for first, last in sorted(ranges):
		if merged and first <= merged[-1][1] + 1:
			merged[-1][1] = max(merged[-1][1], last)
		else:
			merged.append([first, last])
	return merged


def to_networks(ranges, version):
	address = ipaddress.IPv4Address if version == 4 else ipaddress.IPv6Address
	networks = []
	for first, last in merge_ranges(ranges):
		networks.extend(ipaddress.summarize_address_range(address(first), address(last)))
	return networks


def download(url):
	request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
	with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
		data = response.read(MAX_BYTES + 1)
	if len(data) > MAX_BYTES:
		raise ValueError("the file is larger than %d MB" % (MAX_BYTES // 1024 // 1024))
	return data.decode("utf-8", errors="replace")


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


def is_fresh(status, sources):
	try:
		updated = datetime.datetime.fromisoformat(status["updated"])
	except (KeyError, TypeError, ValueError):
		return False
	return status.get("sources") == sources and os.path.isdir(COUNTRIES_DIR) \
		and datetime.datetime.now(datetime.timezone.utc) - updated < datetime.timedelta(hours=MAX_AGE_HOURS)


def update(env, force=False):
	# Downloads the registries' files, unless the data is less than a day old (or force),
	# and writes the networks of each country. Returns a description; raises RuntimeError
	# if a file couldn't be downloaded (the previous data stays in use).
	settings = get_settings(env)
	status = read_status()
	if not force and is_fresh(status, settings["sources"]):
		return "The country data is from %s.\n" % status["updated"]
	now = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
	texts, errors = {}, []
	with concurrent.futures.ThreadPoolExecutor(max_workers=len(settings["sources"])) as pool:
		futures = {pool.submit(download, url): url for url in settings["sources"]}
		for future in concurrent.futures.as_completed(futures):
			try:
				texts[futures[future]] = future.result()
			except Exception as e:
				errors.append("%s: %s" % (futures[future], getattr(e, "reason", None) or e))
	ranges, counts = {}, {}
	for url in settings["sources"]:
		if url in texts:
			counts[url] = parse_registry(texts[url], ranges)
			if not counts[url]:
				errors.append("%s: no allocations in the file" % url)
	if errors:
		status["error"] = "Could not download the registries' data, the previous data stays in use: " + "; ".join(sorted(errors))
		status["checked"] = now
		write_status(status)
		raise RuntimeError(status["error"])

	# Write every country's networks into a new directory, then swap it in.
	new_dir, old_dir = COUNTRIES_DIR + ".new", COUNTRIES_DIR + ".old"
	shutil.rmtree(new_dir, ignore_errors=True)
	os.makedirs(new_dir)
	for code, (v4, v6) in ranges.items():
		with open(os.path.join(new_dir, code + ".txt"), "w") as f:
			for version, version_ranges in ((4, v4), (6, v6)):
				f.writelines("%s\n" % n for n in to_networks(version_ranges, version))
	shutil.rmtree(old_dir, ignore_errors=True)
	if os.path.isdir(COUNTRIES_DIR):
		os.rename(COUNTRIES_DIR, old_dir)
	os.rename(new_dir, COUNTRIES_DIR)
	shutil.rmtree(old_dir, ignore_errors=True)
	write_status({"updated": now, "checked": now, "sources": settings["sources"],
		"allocations": sum(counts.values()), "countries": len(ranges)})
	return "Downloaded the addresses of %d countries.\n" % len(ranges)


def read_country(code):
	# {4: [networks], 6: [networks]} of a country, or None if there is no data for it.
	networks = {4: [], 6: []}
	# Codes are checked when they're added and when the settings are read; this keeps any
	# other value out of the path whoever calls it.
	path = os.path.normpath(os.path.join(COUNTRIES_DIR, "%s.txt" % code))
	if not re.fullmatch(r"[A-Z]{2}", code or "") or not path.startswith(COUNTRIES_DIR + os.sep):
		return None
	try:
		with open(path) as f:
			for line in f:
				try:
					n = ipaddress.ip_network(line.strip())
				except ValueError:
					continue
				networks[n.version].append(n)
	except OSError:
		return None
	return networks


# What the firewall does.

def ports(scope):
	import ip_blocklists
	return list(ip_blocklists.PORTS) + (list(WEB_PORTS) if scope == "web" else [])


def get_rules(env, settings=None):
	# (rules, reason). rules is None when nothing is blocked by country (reason says why),
	# else {"mode", "ports", 4: [networks], 6: [networks]} for ip_blocklists.py.
	settings = settings or get_settings(env)
	if not settings["enabled"]:
		return None, "The country filter is off."
	if not settings["countries"]:
		return None, "No country is chosen, so nothing is blocked by country."
	networks, missing = {4: [], 6: []}, []
	for code in settings["countries"]:
		found = read_country(code)
		if found is None:
			missing.append(code)
			continue
		networks[4].extend(found[4])
		networks[6].extend(found[6])
	if missing:
		return None, ("There is no data for %s yet (download the country data), so nothing is blocked by "
					"country." % ", ".join(missing))
	if settings["mode"] == "allow" and not networks[4] and not networks[6]:
		return None, "The chosen countries have no addresses, so nothing is blocked by country."
	rules = {
		"mode": settings["mode"],
		"ports": ports(settings["scope"]),
		4: list(ipaddress.collapse_addresses(networks[4])),
		6: list(ipaddress.collapse_addresses(networks[6])),
	}
	return rules, None


def nft_chain(rules):
	# The geofence chain of the IP blocklists' table: the input chain jumps to it for the
	# filtered ports, after the exempt addresses have returned.
	lines = ["\tchain geofence {",
		"\t\tip saddr { %s } return" % ", ".join(NOT_PUBLIC[4]),
		"\t\tip6 saddr { %s } return" % ", ".join(NOT_PUBLIC[6])]
	if rules["mode"] == "allow":
		lines += ["\t\tip saddr @geo4 return", "\t\tip6 saddr @geo6 return", "\t\tcounter drop"]
	else:
		lines += ["\t\tip saddr @geo4 counter drop", "\t\tip6 saddr @geo6 counter drop"]
	lines.append("\t}")
	return lines


def is_blocked(rules, address):
	# Whether the filter would drop a connection from an address (ignoring the exempt
	# networks, which ip_blocklists.py checks first).
	if rules is None:
		return False
	ip = ipaddress.ip_address(address)
	if any(ip in ipaddress.ip_network(n) for n in NOT_PUBLIC[ip.version]):
		return False
	listed = any(ip in n for n in rules[ip.version])
	return not listed if rules["mode"] == "allow" else listed


def check_client(env, settings, client_address):
	# Refuses settings that would lock the administrator out of the control panel
	# (with HTTPS filtered, from the address they are using it from).
	if not settings["enabled"] or settings["scope"] != "web" or not client_address:
		return
	try:
		ip = ipaddress.ip_address(client_address)
	except ValueError:
		return
	import ip_blocklists
	if any(ip in n for n in ip_blocklists.get_exempt_networks(env)[ip.version]):
		return
	rules, _ = get_rules(env, settings)
	if is_blocked(rules, ip):
		raise ValueError("That would block your own connection to the control panel (%s): add your country to the "
						"list first, or filter only the login ports." % ip)


# Changes from the control panel. Each one reloads the firewall rules.

def change(env, settings, client_address=None):
	check_client(env, settings, client_address)
	save_settings(env, settings)
	import ip_blocklists
	return ip_blocklists.apply(env)


def set_enabled(env, enabled, client_address=None):
	settings = get_settings(env)
	settings["enabled"] = bool(enabled)
	note = ""
	if settings["enabled"]:
		try:
			update(env)
		except RuntimeError as e:
			note = str(e) + "\n"
	return note + change(env, settings, client_address)


def set_mode(env, mode, client_address=None):
	if mode not in MODES:
		raise ValueError("Unknown mode: %s" % mode)
	settings = get_settings(env)
	settings["mode"] = mode
	return change(env, settings, client_address)


def set_scope(env, scope, client_address=None):
	if scope not in SCOPES:
		raise ValueError("Unknown scope: %s" % scope)
	settings = get_settings(env)
	settings["scope"] = scope
	return change(env, settings, client_address)


def add_country(env, code, client_address=None):
	code = check_country(code)
	settings = get_settings(env)
	if code in settings["countries"]:
		raise ValueError("%s is already in the list." % code)
	settings["countries"].append(code)
	return change(env, settings, client_address)


def remove_country(env, code, client_address=None):
	code = (code or "").strip().upper()
	settings = get_settings(env)
	if code not in settings["countries"]:
		raise ValueError("%s is not in the list." % (code or "The country"))
	settings["countries"].remove(code)
	return change(env, settings, client_address)


def get_dropped():
	import ip_blocklists
	try:
		out = subprocess.run(["/usr/sbin/nft", "list", "chain", "inet", ip_blocklists.TABLE, "geofence"],
							capture_output=True, text=True, timeout=10).stdout
	except (OSError, subprocess.SubprocessError):
		return None
	counts = [int(n) for n in re.findall(r"counter packets (\d+)", out)]
	return sum(counts) if counts else None


def get_status(env):
	import ip_blocklists
	settings = get_settings(env)
	state = read_status()
	rules, reason = get_rules(env, settings)
	names = country_names()
	chosen = []
	for code in settings["countries"]:
		found = read_country(code)
		chosen.append({"code": code, "name": names.get(code, code),
			"networks": None if found is None else len(found[4]) + len(found[6])})
	return {
		"available": ip_blocklists.is_available(),
		"enabled": settings["enabled"],
		"mode": settings["mode"],
		"scope": settings["scope"],
		"ports": ports(settings["scope"]),
		"countries": chosen,
		"choices": [{"code": c, "name": names.get(c, c)} for c in known_countries()],
		"updated": state.get("updated"),
		"error": state.get("error") if state.get("checked") != state.get("updated") else None,
		"active": rules is not None,
		"reason": reason,
		"networks": len(rules[4]) + len(rules[6]) if rules else 0,
		"dropped": get_dropped() if rules and ip_blocklists.is_available() else None,
	}


if __name__ == "__main__":
	env = utils.load_environment()
	args = sys.argv[1:]
	try:
		if args == ["update"]:
			print(update(env), end="")
		elif args == ["update", "--force"]:
			print(update(env, force=True), end="")
		elif args == ["status"]:
			print(json.dumps(get_status(env), indent=1))
		elif args in (["on"], ["off"]):
			print(set_enabled(env, args[0] == "on"), end="")
		elif len(args) == 2 and args[0] == "mode":
			print(set_mode(env, args[1]), end="")
		elif len(args) == 2 and args[0] == "scope":
			print(set_scope(env, args[1]), end="")
		elif len(args) == 2 and args[0] in ("add", "remove"):
			print((add_country if args[0] == "add" else remove_country)(env, args[1]), end="")
		else:
			print("Usage: %s update [--force] | status | on | off | mode allow|block | scope logins|web | add CC | remove CC"
				% sys.argv[0])
			sys.exit(2)
	except (RuntimeError, ValueError) as e:
		print(e, file=sys.stderr)
		sys.exit(1)
