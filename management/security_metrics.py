#!/usr/local/lib/mailinabox/env/bin/python
#
# Security metrics for the control panel's dashboard: attacks on the box over time.
#
# * Failed logins, from the mail logs (also the rotated and compressed ones): password
#   failures when sending mail (ports 465 and 587, Postfix) and on IMAP, POP3 and Sieve
#   (Dovecot, which logs how many attempts each connection made).
# * Bans, from fail2ban's logs, by jail.
# * Connections dropped by the IP blocklists (see ip_blocklists.py) and by the country
#   filter (see geofence.py). nftables only keeps running totals, so the hourly job
#   (`collect`, see setup/management.sh) samples them and the chart starts filling from
#   the first sample. The country filter's HTTPS requests come from nginx's log instead.
# * The addresses behind most failures, with where they are (looked up by `collect` on
#   RIPEstat, like login_alerts.py, only the address is sent), whether fail2ban bans them
#   now and whether they are on the blocklists.
#
# Times are bucketed by hour (last 24 hours) or by day (7 or 30 days), in UTC: the panel
# shows them in the browser's time zone.
########################################################################

import bisect
import collections
import datetime
import glob
import gzip
import ipaddress
import json
import os
import re
import subprocess
import sys
import time

import utils
import login_alerts

STATE_FILE = "/var/lib/mailinabox/security-metrics.json"
FAIL2BAN_LOG = "/var/log/fail2ban.log"
KEEP_DAYS = 35
TOP = 15
LOOKUPS_PER_RUN = 15
RANGES = {"24h": (24, 3600), "7d": (7, 86400), "30d": (30, 86400)}

POSTFIX_FAIL_RE = re.compile(
	r"postfix/submission/smtpd\[\d+\]: warning: [^\[\s]*\[([0-9A-Fa-f.:]+)\]: SASL \S+ authentication failed")
DOVECOT_FAIL_RE = re.compile(
	r"(imap|pop3|managesieve)-login: (?:Info: )?(?:Disconnected|Aborted login)[^(]*\(auth failed, (\d+) attempts?[^)]*\).*?rip=([0-9A-Fa-f.:]+)")
FAIL2BAN_BAN_RE = re.compile(
	r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}),\d+ fail2ban\.actions\s+\[\d+\]: NOTICE\s+\[([^\]]+)\] Ban ([0-9A-Fa-f.:]+)")
SERVICES = {"imap": "IMAP/POP", "pop3": "IMAP/POP", "managesieve": "IMAP/POP"}

_cache = {}


def now():
	return datetime.datetime.now(datetime.timezone.utc)


def load_state():
	try:
		with open(STATE_FILE) as f:
			state = json.load(f)
		if isinstance(state, dict):
			state.setdefault("samples", [])
			state.setdefault("locations", {})
			return state
	except (OSError, ValueError):
		pass
	return {"samples": [], "locations": {}}


def save_state(state):
	os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
	with open(STATE_FILE + ".tmp", "w") as f:
		json.dump(state, f)
	os.chmod(STATE_FILE + ".tmp", 0o600)
	os.replace(STATE_FILE + ".tmp", STATE_FILE)


def rotated(path):
	# A log and its rotated copies, oldest first.
	def rotation(fn):
		m = re.search(r"\.(\d+)(\.gz)?$", fn)
		return -int(m.group(1)) if m else 0
	return sorted(glob.glob(path + "*"), key=rotation)


def read_lines(fn):
	try:
		opener = gzip.open if fn.endswith(".gz") else open
		with opener(fn, "rt", errors="replace") as f:
			for line in f:
				yield line
	except (OSError, EOFError):
		return


def failed_logins(since):
	# [(time, address, service, attempts)] since the given time (an aware datetime).
	reference = now()
	since_iso = since.isoformat(timespec="seconds")
	events = []
	for fn in rotated(login_alerts.MAIL_LOG):
		for line in read_lines(fn):
			if "authentication failed" in line:
				m = POSTFIX_FAIL_RE.search(line)
				if m:
					stamp = login_alerts.line_time(line, reference)
					if stamp and stamp >= since_iso:
						events.append((stamp, m.group(1), "SMTP", 1))
			elif "auth failed" in line:
				m = DOVECOT_FAIL_RE.search(line)
				if m:
					stamp = login_alerts.line_time(line, reference)
					if stamp and stamp >= since_iso:
						events.append((stamp, m.group(3), SERVICES[m.group(1)], int(m.group(2))))
	return events


def bans(since):
	# [(time, jail, address)] since the given time.
	since_iso = since.isoformat(timespec="seconds")
	events = []
	for fn in rotated(FAIL2BAN_LOG):
		for line in read_lines(fn):
			if "] Ban " not in line:
				continue
			m = FAIL2BAN_BAN_RE.match(line)
			if not m:
				continue
			try:
				stamp = datetime.datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S").astimezone() \
					.astimezone(datetime.timezone.utc).isoformat(timespec="seconds")
			except ValueError:
				continue
			if stamp >= since_iso:
				events.append((stamp, m.group(2), m.group(3)))
	return events


def banned_now():
	# {address: [jails]} of the addresses fail2ban bans right now.
	try:
		out = subprocess.run(["fail2ban-client", "banned"], capture_output=True, text=True, timeout=30).stdout
	except (OSError, subprocess.SubprocessError):
		return {}
	result = collections.defaultdict(list)
	for jail, addresses in re.findall(r"'([^']+)': \[([^\]]*)\]", out):
		for address in re.findall(r"'([^']+)'", addresses):
			result[address].append(jail)
	return result


def blocklist_matcher():
	# A function telling whether an address is on the IP blocklists.
	try:
		import ip_blocklists
		merged = ip_blocklists.read_merged()
	except Exception:
		return lambda address: False
	ranges = {}
	for version, networks in merged.items():
		starts = [int(n.network_address) for n in networks]
		ends = [int(n.broadcast_address) for n in networks]
		ranges[version] = (starts, ends)

	def matches(address):
		try:
			ip = ipaddress.ip_address(address)
		except ValueError:
			return False
		starts, ends = ranges.get(ip.version, ([], []))
		i = bisect.bisect_right(starts, int(ip)) - 1
		return i >= 0 and int(ip) <= ends[i]
	return matches


def dropped_totals():
	# The running totals of the drop counters of the blocklists and of the country filter
	# (login ports), each None if there is none.
	totals = []
	for module in ("ip_blocklists", "geofence"):
		try:
			totals.append(__import__(module).get_dropped())
		except Exception:
			totals.append(None)
	return totals


def web_blocked_series(starts, size):
	# HTTPS requests the country filter dropped per bucket, from nginx's log.
	values = [0] * len(starts)
	try:
		import geofence
		events = geofence.web_blocked(starts[0])
	except Exception:
		events = []
	for stamp, _, _ in events:
		i = bucket_index(starts, size, stamp)
		if i is not None:
			values[i] += 1
	return values


def collect(env):
	# Run every hour: samples the drop counters and looks up where the top attackers of
	# the last day are.
	state = load_state()
	blocklists, geo = dropped_totals()
	if blocklists is not None or geo is not None:
		sample = {"time": now().isoformat(timespec="seconds"), "v": 2}
		if blocklists is not None:
			sample["dropped"] = blocklists
		if geo is not None:
			sample["geo"] = geo
		state["samples"].append(sample)
	limit = (now() - datetime.timedelta(days=KEEP_DAYS)).isoformat(timespec="seconds")
	state["samples"] = [s for s in state["samples"] if s.get("time", "") >= limit]

	if login_alerts.get_config(env)["locate"]:
		counts = collections.Counter()
		for _, address, _, attempts in failed_logins(now() - datetime.timedelta(days=1)):
			counts[address] += attempts
		for _, _, address in bans(now() - datetime.timedelta(days=1)):
			counts[address] += 1
		looked_up = 0
		for address, _ in counts.most_common(TOP * 2):
			if address in state["locations"] or looked_up >= LOOKUPS_PER_RUN:
				continue
			location = login_alerts.lookup_location(address)
			looked_up += 1
			if location is not None:
				location["checked"] = now().isoformat(timespec="seconds")
				state["locations"][address] = location
		old = (now() - datetime.timedelta(days=KEEP_DAYS)).isoformat(timespec="seconds")
		state["locations"] = {a: l for a, l in state["locations"].items() if l.get("checked", "") >= old}
	save_state(state)


def bucket_starts(range_name):
	count, size = RANGES[range_name]
	end = now()
	if size == 3600:
		last = end.replace(minute=0, second=0, microsecond=0)
	else:
		# Days in the box's local time zone, so that a bar is a calendar day there.
		local = end.astimezone()
		last = local.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(datetime.timezone.utc)
	return [last - datetime.timedelta(seconds=size * i) for i in range(count - 1, -1, -1)], size


def bucket_index(starts, size, stamp):
	when = datetime.datetime.fromisoformat(stamp)
	if when < starts[0]:
		return None
	i = bisect.bisect_right(starts, when) - 1
	return i if 0 <= i < len(starts) else None


def dropped_series(state, starts, size, key="dropped"):
	# Connections dropped per bucket, from the samples of the running total (which
	# restarts from zero when the rules are loaded again). key: "dropped" for the IP
	# blocklists, "geo" for the country filter on the login ports. Samples before v61.20
	# had both in "dropped" (no "v"), so a pair across that change is skipped.
	values = [None] * len(starts)
	samples = sorted((s for s in state["samples"] if isinstance(s.get(key), int)), key=lambda s: s["time"])
	for a, b in zip(samples, samples[1:]):
		if a.get("v") != b.get("v"):
			continue
		delta = b[key] - a[key]
		if delta < 0:
			delta = b[key]  # the counter was reset
		i = bucket_index(starts, size, b["time"])
		if i is not None:
			values[i] = (values[i] or 0) + delta
	first = samples[0]["time"] if samples else None
	return values, first


def get_metrics(env, range_name="24h", fresh=False):
	if range_name not in RANGES:
		range_name = "24h"
	cached = _cache.get(range_name)
	if cached and time.time() - cached[0] < 120 and not fresh:
		return cached[1]

	starts, size = bucket_starts(range_name)
	since = starts[0]
	previous_since = since - (starts[-1] + datetime.timedelta(seconds=size) - since)
	state = load_state()

	failures = failed_logins(previous_since)
	ban_events = bans(previous_since)
	since_iso = since.isoformat(timespec="seconds")

	services = ["SMTP", "IMAP/POP"]
	failed = {s: [0] * len(starts) for s in services}
	attackers = collections.Counter()
	attacker_services = collections.defaultdict(set)
	last_seen = {}
	previous = {"failed": 0, "bans": 0, "attackers": set()}
	for stamp, address, service, attempts in failures:
		if stamp < since_iso:
			previous["failed"] += attempts
			previous["attackers"].add(address)
			continue
		i = bucket_index(starts, size, stamp)
		if i is None:
			continue
		failed[service][i] += attempts
		attackers[address] += attempts
		attacker_services[address].add(service)
		last_seen[address] = max(last_seen.get(address, ""), stamp)

	jails = collections.Counter(jail for stamp, jail, _ in ban_events if stamp >= since_iso)
	top_jails = [jail for jail, _ in jails.most_common(3)]
	jail_series = {jail: [0] * len(starts) for jail in top_jails}
	if len(jails) > 3:
		jail_series["Other"] = [0] * len(starts)
	banned_addresses = set()
	for stamp, jail, address in ban_events:
		if stamp < since_iso:
			previous["bans"] += 1
			continue
		i = bucket_index(starts, size, stamp)
		if i is None:
			continue
		jail_series[jail if jail in top_jails else "Other"][i] += 1
		banned_addresses.add(address)
		last_seen[address] = max(last_seen.get(address, ""), stamp)
		if address not in attackers:
			attackers[address] += 0
		attacker_services[address].add("ban: " + jail)

	dropped, dropped_since = dropped_series(state, starts, size)
	# The country filter: the firewall's drops on the login ports and nginx's on HTTPS.
	geo_dropped, _ = dropped_series(state, starts, size, "geo")
	for i, n in enumerate(web_blocked_series(starts, size)):
		if n:
			geo_dropped[i] = (geo_dropped[i] or 0) + n

	now_banned = banned_now()
	on_blocklist = blocklist_matcher()
	try:
		import ip_blocklists
		blocked_by_you = ip_blocklists.manual_matcher(env)
	except Exception:
		blocked_by_you = lambda address: None
	ranked = sorted(attackers.items(), key=lambda kv: (-kv[1], -len(attacker_services[kv[0]]), kv[0]))
	ban_counts = collections.Counter(address for stamp, _, address in ban_events if stamp >= since_iso)
	top = []
	for address, count in ranked[:TOP]:
		top.append({
			"address": address,
			"failed": count,
			"bans": ban_counts.get(address, 0),
			"services": sorted(s for s in attacker_services[address] if not s.startswith("ban: ")),
			"jails": sorted(s[5:] for s in attacker_services[address] if s.startswith("ban: ")),
			"last": last_seen.get(address),
			"banned_now": now_banned.get(address, []),
			"on_blocklist": on_blocklist(address),
			"blocked_by_you": blocked_by_you(address),
			"location": state["locations"].get(address),
		})

	countries = collections.Counter()
	for address, count in attackers.items():
		location = state["locations"].get(address)
		if location and location.get("country"):
			countries[location["country"]] += max(count, 1)

	result = {
		"range": range_name,
		"bucket_seconds": size,
		"buckets": [s.isoformat(timespec="seconds") for s in starts],
		"failed": failed,
		"bans": jail_series,
		"dropped": dropped,
		"geo_dropped": geo_dropped,
		"dropped_since": dropped_since,
		"totals": {
			"failed": sum(sum(v) for v in failed.values()),
			"bans": sum(jails.values()),
			"attackers": len(attackers),
			"dropped": sum(v for v in dropped if v) if any(v is not None for v in dropped) else None,
			"geo_dropped": sum(v for v in geo_dropped if v) if any(v is not None for v in geo_dropped) else None,
			"banned_now": len(now_banned),
		},
		"previous": {
			"failed": previous["failed"],
			"bans": previous["bans"],
			"attackers": len(previous["attackers"]),
		},
		"top": top,
		"countries": [{"country": c, "count": n} for c, n in countries.most_common()],
		"located_from": "RIPEstat" if login_alerts.get_config(env)["locate"] else None,
	}
	_cache[range_name] = (time.time(), result)
	return result


if __name__ == "__main__":
	env = utils.load_environment()
	if len(sys.argv) == 2 and sys.argv[1] == "collect":
		collect(env)
	elif len(sys.argv) in (2, 3) and sys.argv[1] == "show":
		print(json.dumps(get_metrics(env, sys.argv[2] if len(sys.argv) == 3 else "24h"), indent=1))
	else:
		print("Usage: %s collect | show [24h|7d|30d]" % sys.argv[0])
		sys.exit(2)
