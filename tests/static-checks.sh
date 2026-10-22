#!/bin/bash
# Quick checks that don't need a server: syntax of the setup scripts, of the
# management daemon and of the control panel's JavaScript. Takes seconds.
# (The full tests, which install Power Mail-in-a-Box in containers, are in
# tests/container/ and are run before releasing a new version.)
# usage: tests/static-checks.sh
cd "$(dirname "$0")/.."
fail=0
bad() { echo "FAIL: $1"; fail=1; }

# Bash: the setup scripts, tools and test scripts.
for f in setup/*.sh management/*.sh tests/container/*.sh $(grep -l '^#!/bin/bash' tools/* 2>/dev/null); do
	# tools/ssl_cleanup uses extglob patterns, which bash -n can't parse without it.
	bash -O extglob -n "$f" 2>/dev/null || bad "bash syntax: $f"
done

# Python: the management daemon and tools.
pycache=$(mktemp -d)
for f in management/*.py setup/migrate.py tools/*.py; do
	PYTHONPYCACHEPREFIX=$pycache python3 -W ignore -m py_compile "$f" 2>/dev/null || bad "python syntax: $f"
done
rm -rf "$pycache"

# JavaScript in the control panel's templates (with Jinja expressions replaced).
if command -v node >/dev/null; then
	tmp=$(mktemp -d)
	for f in management/templates/*.html; do
		python3 - "$f" "$tmp/check.js" <<-'EOF'
			import re, sys
			s = open(sys.argv[1]).read()
			s = re.sub(r"\{\{[^}]*tojson[^}]*\}\}", "null", s)
			s = re.sub(r"\{\{.*?\}\}", "x", s)
			s = re.sub(r"\{%.*?%\}", "", s)
			blocks = re.findall(r"<script>(.*?)</script>", s, re.S)
			open(sys.argv[2], "w").write("\n;\n".join(blocks))
		EOF
		node --check "$tmp/check.js" 2>/dev/null || bad "javascript syntax: $f"
	done
	rm -rf "$tmp"
else
	echo "(node is not installed: skipping the JavaScript checks)"
fi

# Every download is verified against a hash.
grep -n 'wget_verify' setup/*.sh | grep -v 'function wget_verify\|^setup/functions.sh' \
	| grep -vE 'wget_verify[[:space:]]+\\?$' | grep -vE 'wget_verify .+ .+ .+' \
	| while read -r line; do bad "download without a hash: $line"; done

[ $fail = 0 ] && echo "All static checks passed."
exit $fail
