#!/usr/local/lib/mailinabox/env/bin/python
#
# The mail servers and senders that the administrator allows or blocks in the
# control panel (Mail > Filtering).
#
# * Allowed servers skip greylisting. Postgrey defers the first delivery
#   attempt of every new combination of server, sender and recipient (see
#   setup/mail-postfix.sh), and senders that use a different return address
#   for every message (notifications, newsletters) are always new, so their
#   mail only arrives when they try again, sometimes an hour later. Entries
#   are host names, which also match their subdomains, or IP addresses and
#   networks. Postfix only passes the server's host name on to postgrey when
#   its reverse DNS resolves back to its address, so unlike the sender address
#   it can't be forged. The other checks (Spamhaus, SPF, DKIM, DMARC and the
#   spam filter) still apply. The list is written to
#   /etc/postgrey/whitelist_clients.local, which postgrey reads besides the
#   list that setup/mail-postfix.sh downloads.
#
# * Mail from blocked senders and servers is refused before it is accepted
#   ("554 5.7.1"). Entries are e-mail addresses and domains (which also match
#   their subdomains), matched against the envelope sender (the return path),
#   or IP addresses and networks of the sending server. The list is written to
#   /etc/postfix/blocked_senders and /etc/postfix/blocked_clients.cidr, which
#   smtpd_recipient_restrictions checks after letting the box's own users
#   through and before the other checks (see setup/mail-postfix.sh).
#
# The lists are stored in the "mail_filtering" key of
# $STORAGE_ROOT/settings.yaml. Empty lists change nothing.
########################################################################

import collections
import ipaddress
import os
import re
import shutil
import sys

import utils

LISTS = ("allow", "block")

POSTGREY_FILE = "/etc/postgrey/whitelist_clients.local"
SENDERS_FILE = "/etc/postfix/blocked_senders"
CLIENTS_FILE = "/etc/postfix/blocked_clients.cidr"
POSTFIX_CHECKS = [
	"check_client_access cidr:" + CLIENTS_FILE,
	"check_sender_access hash:" + SENDERS_FILE,
]
HEADER = "# Written by Mail-in-a-Box from the lists in the control panel (Mail > Filtering).\n# Changes made here are lost: edit the lists in the control panel instead.\n"
REJECT = "REJECT Blocked by the administrator of this mail server"

MAIL_LOG = "/var/log/mail.log"
# Servers whose mail was held back at least this long (in seconds) by
# greylisting are suggested for the allow list.
SUGGEST_DELAY = 600

DOMAIN_RE = re.compile(
	r"^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+(?:[a-z]{2,63}|xn--[a-z0-9-]{1,59})$")
LOCAL_PART_RE = re.compile(r'^[^\s@<>()\[\],;:"\\]{1,64}$')


def normalize_domain(domain, entry):
	domain = domain.strip().lower().rstrip(".")
	if domain.startswith("*."):
		domain = domain[2:]
	elif domain.startswith("."):
		domain = domain[1:]
	try:
		domain = domain.encode("idna").decode("ascii")
	except UnicodeError:
		raise ValueError("%s is not a valid domain name." % entry)
	if not DOMAIN_RE.match(domain):
		raise ValueError("%s is not a valid domain name." % entry)
	return domain


def get_box_domains(env):
	from mailconfig import get_mail_domains
	return set(get_mail_domains(env)) | {env["PRIMARY_HOSTNAME"]}


def parse_entry(entry, list_name, box_domains=()):
	# Validates and normalizes an entry of a list. Returns (entry, kind), kind
	# being "network" (an IP address or network), "host" (a server's host name,
	# in the allow list), "domain" or "address" (of a sender, in the block list).
	entry = entry.strip()
	if not entry:
		raise ValueError("The entry is empty.")

	try:
		network = ipaddress.ip_network(entry, strict=False)
	except ValueError:
		network = None
	if network is not None:
		if network.prefixlen < (8 if network.version == 4 else 16):
			raise ValueError("%s is too large a network." % entry)
		if network.num_addresses == 1:
			return str(network.network_address), "network"
		return str(network), "network"

	if "@" in entry:
		if list_name == "allow":
			raise ValueError(
				"%s is an e-mail address. Add the mail server it comes from instead (its host name or IP address): "
				"sender addresses can be forged." % entry)
		local_part, domain = entry.rsplit("@", 1)
		if not LOCAL_PART_RE.match(local_part):
			raise ValueError("%s is not a valid e-mail address." % entry)
		return local_part.lower() + "@" + normalize_domain(domain, entry), "address"

	domain = normalize_domain(entry, entry)
	if list_name == "allow":
		return domain, "host"
	for box_domain in box_domains:
		if box_domain == domain or box_domain.endswith("." + domain):
			raise ValueError(
				"%s can't be blocked: this box receives mail for %s." % (entry, box_domain))
	return domain, "domain"


def split_entries(entries):
	# Entries are given as a list, or as a string separated by spaces, commas or
	# new lines.
	if isinstance(entries, str):
		entries = re.split(r"[\s,]+", entries)
	return [entry for entry in entries if entry.strip()]


def read_unmanaged_postgrey_file():
	# The entries of a whitelist_clients.local that the administrator wrote
	# before the lists existed (or None if there isn't one), and how many of its
	# lines can't be imported (e.g. regular expressions).
	try:
		with open(POSTGREY_FILE) as f:
			text = f.read()
	except FileNotFoundError:
		return None, 0
	if text.startswith(HEADER):
		return None, 0
	entries, skipped = [], 0
	for line in text.split("\n"):
		line = re.sub(r"#.*", "", line).strip()
		if not line:
			continue
		try:
			entry, _ = parse_entry(line, "allow")
		except ValueError:
			skipped += 1
			continue
		if entry not in entries:
			entries.append(entry)
	return entries, skipped


def get_lists(env):
	# Returns {list name: [entries]}. Before the administrator saves the lists
	# for the first time, the allow list holds what they had added by hand to
	# the postgrey file, if anything.
	config = utils.load_settings(env).get("mail_filtering")
	if not isinstance(config, dict):
		imported, _ = read_unmanaged_postgrey_file()
		return {"allow": imported or [], "block": []}
	lists = {}
	for name in LISTS:
		entries = config.get(name)
		lists[name] = []
		for entry in entries if isinstance(entries, list) else []:
			try:
				entry, _ = parse_entry(str(entry), name)
			except ValueError:
				continue  # edited by hand
			if entry not in lists[name]:
				lists[name].append(entry)
	return lists


def is_managed(env):
	return isinstance(utils.load_settings(env).get("mail_filtering"), dict)


def set_list(name, entries, env):
	# Replaces a list. Returns a description of what was done.
	if name not in LISTS:
		raise ValueError("Unknown list: %s" % name)
	box_domains = get_box_domains(env) if name == "block" else ()
	result = []
	for entry in split_entries(entries):
		entry, _ = parse_entry(entry, name, box_domains)
		if entry not in result:
			result.append(entry)
	return save_lists(env, **{name: result})


def add_entry(name, entry, env):
	if name not in LISTS:
		raise ValueError("Unknown list: %s" % name)
	entry, _ = parse_entry(entry, name,
						get_box_domains(env) if name == "block" else ())
	entries = get_lists(env)[name]
	if entry in entries:
		raise ValueError("%s is already in the list." % entry)
	return save_lists(env, **{name: entries + [entry]})


def remove_entry(name, entry, env):
	if name not in LISTS:
		raise ValueError("Unknown list: %s" % name)
	entries = get_lists(env)[name]
	try:
		entry, _ = parse_entry(entry, name)
	except ValueError:
		entry = entry.strip()
	if entry not in entries:
		raise ValueError("%s is not in the list." % entry)
	return save_lists(env, **{name: [e for e in entries if e != entry]})


def save_lists(env, **changes):
	lists = get_lists(env)
	lists.update(changes)
	config = utils.load_settings(env)
	config["mail_filtering"] = lists
	utils.write_settings(config, env)
	return apply_filtering(env)


def get_filtering_status(env):
	lists = get_lists(env)
	managed = is_managed(env)
	_, skipped = (None, 0) if managed else read_unmanaged_postgrey_file()
	status = {
		name: [{
			"entry": entry,
			"kind": parse_entry(entry, name)[1]
		} for entry in lists[name]]
		for name in LISTS
	}
	status.update({
		# Entries found in a postgrey file written by hand, not saved yet.
		"imported": not managed and bool(lists["allow"]),
		"not_imported": skipped,
		"delayed": get_delayed_servers(lists["allow"]),
		"postgrey_file": POSTGREY_FILE,
	})
	return status


def suggested_domain(host):
	# The domain to allow for a server, e.g. x.com for spring-chicken-ao.x.com.
	# Without a list of public suffixes, guess that a two-letter top-level
	# domain under a short generic second-level name (co.uk, com.au) takes one
	# more label.
	labels = host.split(".")
	keep = 3 if len(labels) >= 3 and len(labels[-1]) == 2 and labels[-2] in (
		"co", "com", "net", "org", "gov", "edu", "ac", "ne", "or", "go") else 2
	return ".".join(labels[-keep:])


def is_allowed(host, address, allow):
	for entry in allow:
		if "/" in entry or ":" in entry or entry.replace(".", "").isdigit():
			try:
				if ipaddress.ip_address(address) in ipaddress.ip_network(entry, strict=False):
					return True
			except ValueError:
				pass
		elif host == entry or host.endswith("." + entry):
			return True
	return False


def get_delayed_servers(allow, log_file=MAIL_LOG):
	# The servers whose mail was held back a while by greylisting according to
	# the mail log, grouped by the domain to allow, with the longest delay.
	pattern = re.compile(
		r"postgrey\[\d+\]: action=pass, reason=triplet found, delay=(\d+), client_name=([^,]+), client_address=([^/,]+)")
	servers = collections.OrderedDict()
	try:
		with open(log_file, errors="replace") as f:
			for line in f:
				if "reason=triplet found, delay=" not in line:
					continue
				m = pattern.search(line)
				if not m or int(m.group(1)) < SUGGEST_DELAY:
					continue
				delay, host, address = int(m.group(1)), m.group(2).lower(), m.group(3)
				if host == "unknown" or is_allowed(host, address, allow):
					continue
				domain = suggested_domain(host)
				server = servers.setdefault(domain, {
					"domain": domain,
					"hosts": [],
					"messages": 0,
					"longest_delay": 0
				})
				server["messages"] += 1
				server["longest_delay"] = max(server["longest_delay"], delay)
				server["last_seen"] = line[:15]
				if host not in server["hosts"] and len(server["hosts"]) < 3:
					server["hosts"].append(host)
	except (FileNotFoundError, PermissionError):
		return []
	return sorted(servers.values(), key=lambda s: -s["messages"])[:20]


def write_file(fn, content):
	# Writes the file atomically, if it changed. Returns whether it changed.
	if os.path.exists(fn):
		with open(fn) as f:
			if f.read() == content:
				return False
	with open(fn + ".tmp", "w") as f:
		f.write(content)
	os.chmod(fn + ".tmp", 0o644)
	os.replace(fn + ".tmp", fn)
	return True


def ensure_postfix_checks():
	# Adds the checks of the block list to smtpd_recipient_restrictions on boxes
	# that were set up before the lists existed, right after letting the box's
	# own users through (the setup puts them there too). Returns whether it
	# changed anything.
	restrictions = utils.shell("check_output",
							["postconf", "-h", "smtpd_recipient_restrictions"]).strip()
	parts = [p.strip() for p in restrictions.split(",") if p.strip()]
	if all(check in parts for check in POSTFIX_CHECKS):
		return False
	parts = [p for p in parts if p not in POSTFIX_CHECKS]
	position = 0
	for permit in ("permit_mynetworks", "permit_sasl_authenticated"):
		if permit in parts:
			position = max(position, parts.index(permit) + 1)
	parts[position:position] = POSTFIX_CHECKS
	utils.shell("check_call", ["postconf", "-e", "smtpd_recipient_restrictions=" + ",".join(parts)])
	return True


def apply_filtering(env):
	# Writes the lists to the files postgrey and Postfix read, and reloads them
	# if they changed. Returns a description of the lists.
	lists = get_lists(env)
	messages = []

	if is_managed(env):
		_, skipped = read_unmanaged_postgrey_file()
		if os.path.exists(POSTGREY_FILE) and not open(POSTGREY_FILE).read().startswith(HEADER):
			# Keep the file the administrator wrote by hand.
			shutil.copy2(POSTGREY_FILE, POSTGREY_FILE + ".before-mailinabox")
			messages.append("The previous %s was saved as %s.before-mailinabox%s." %
							(POSTGREY_FILE, POSTGREY_FILE,
							" (%d of its lines aren't host names or IP addresses and weren't imported)" % skipped if skipped else ""))
		if lists["allow"]:
			changed = write_file(POSTGREY_FILE, HEADER + "".join(e + "\n" for e in lists["allow"]))
		elif os.path.exists(POSTGREY_FILE):
			os.unlink(POSTGREY_FILE)
			changed = True
		else:
			changed = False
		if changed:
			utils.shell("check_call", ["/usr/sbin/service", "postgrey", "reload"])

	senders, clients = [], []
	for entry in lists["block"]:
		(clients if parse_entry(entry, "block")[1] == "network" else senders).append(entry)
	changed = write_file(SENDERS_FILE, HEADER + "".join("%s %s\n" % (e, REJECT) for e in senders))
	if changed or not os.path.exists(SENDERS_FILE + ".db"):
		utils.shell("check_call", ["/usr/sbin/postmap", SENDERS_FILE])
	changed = write_file(CLIENTS_FILE, HEADER + "".join("%s %s\n" % (e, REJECT) for e in clients)) or changed
	# The files exist now, so Postfix can be told to read them.
	changed = ensure_postfix_checks() or changed
	if changed:
		utils.shell("check_call", ["/usr/sbin/postfix", "reload"], capture_stderr=True)

	messages.append("Allowed servers: %d. Blocked senders and servers: %d." %
					(len(lists["allow"]), len(lists["block"])))
	return "\n".join(messages) + "\n"


if __name__ == "__main__":
	env = utils.load_environment()
	if len(sys.argv) == 2 and sys.argv[1] == "apply":
		print(apply_filtering(env), end="")
	elif len(sys.argv) == 2 and sys.argv[1] == "list":
		lists = get_lists(env)
		for name in LISTS:
			for entry in lists[name]:
				print("%-5s %s" % (name, entry))
	elif len(sys.argv) == 4 and sys.argv[1] in LISTS and sys.argv[2] in ("add", "remove"):
		# Works without the management daemon.
		try:
			print((add_entry if sys.argv[2] == "add" else remove_entry)(sys.argv[1], sys.argv[3], env), end="")
		except ValueError as e:
			print(e, file=sys.stderr)
			sys.exit(1)
	else:
		print("Usage: %s apply | list | {allow,block} {add,remove} ENTRY" % sys.argv[0])
		sys.exit(2)
