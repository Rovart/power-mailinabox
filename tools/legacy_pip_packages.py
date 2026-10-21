#!/usr/bin/python3
#
# Previous versions of Mail-in-a-Box installed duplicity, b2sdk, boto3 and
# email_validator system-wide with pip (in /usr/local/lib/python3.X). Besides
# being disallowed by modern distributions (PEP 668), this also replaced some
# of the distribution's own Python packages with incompatible versions (e.g.
# cryptography, breaking certbot).
#
# This prints the names of those packages and of the dependencies they pulled
# in (and that nothing else installed there needs), so that they can be
# uninstalled.

import importlib.metadata
import re

ROOTS = {"duplicity", "b2sdk", "boto3", "email-validator"}


def normalize(name):
	return re.sub(r"[-_.]+", "-", name).lower()


# Distributions installed system-wide with pip.
dists = {}
for dist in importlib.metadata.distributions():
	path = str(dist.locate_file(""))
	if path.startswith("/usr/local/lib/python3"):
		dists[normalize(dist.metadata["Name"])] = dist


def requirements(name):
	for req in dists[name].requires or []:
		if "extra ==" in req:
			continue  # optional dependency
		m = re.match(r"\s*([A-Za-z0-9][A-Za-z0-9._-]*)", req)
		if m and normalize(m.group(1)) in dists:
			yield normalize(m.group(1))


# The packages we installed and everything they depend on...
remove = set()
pending = [name for name in ROOTS if name in dists]
while pending:
	name = pending.pop()
	if name not in remove:
		remove.add(name)
		pending.extend(requirements(name))

# ...except what's still needed by other packages installed there.
keep = set(dists) - remove
pending = list(keep)
while pending:
	for dep in requirements(pending.pop()):
		if dep in remove:
			remove.discard(dep)
			keep.add(dep)
			pending.append(dep)

print(" ".join(sorted(remove)))
