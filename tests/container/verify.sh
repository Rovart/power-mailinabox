#!/bin/bash
# Functional checks of an installed box (run inside the container).
# Expected versions, from the setup scripts being tested.
PHP_VER=$(sed -n 's/^PHP_VER=//p' /root/mailinabox/setup/functions.sh)
NC_MAJOR=$(sed -n 's/^nextcloud_ver=\([0-9]*\)\..*/\1/p' /root/mailinabox/setup/nextcloud.sh)
ZPUSH_VER=$(sed -n 's/^VERSION=//p' /root/mailinabox/setup/zpush.sh)
NC_APPS=$(sed -n 's/^\(contacts\|calendar\|user_external\)_ver=/\1=/p' /root/mailinabox/setup/nextcloud.sh)
U=me@example.com
P='Secret123!pw'
H=https://127.0.0.1
C="curl -sk --max-time 60"
pass=0; fail=0
ok()  { echo "PASS: $1"; pass=$((pass+1)); }
bad() { echo "FAIL: $1"; fail=$((fail+1)); }
check() { if eval "$2"; then ok "$1"; else bad "$1"; fi; }

echo "== versions"
php -v | head -1
lsb_release -ds
dovecot --version
nginx -v 2>&1
postconf -h mail_version
/usr/local/bin/duplicity --version
/usr/local/lib/mailinabox/env/bin/python -c "import flask, cryptography, dns.version; print('flask', flask.__version__ if hasattr(flask,'__version__') else '', 'cryptography', cryptography.__version__, 'dnspython', dns.version.version)" 2>/dev/null

echo "== services"
check "no failed systemd units" '[ -z "$(systemctl --failed --no-legend --plain | grep -v -e systemd-sysctl -e systemd-remount -e systemd-modules-load -e console -e getty)" ] || { systemctl --failed --no-legend; false; }'
for s in nginx php$PHP_VER-fpm dovecot postfix spampd opendkim opendmarc nsd bind9 fail2ban mailinabox postgrey munin-node cron; do
	check "service $s active" "systemctl is-active --quiet $s"
done
if command -v ufw >/dev/null; then
	check "firewall (ufw) active" 'ufw status | grep -q "^Status: active"'
	check "TCP packets with IPv4 options dropped (CVE-2026-98432)" 'systemctl is-active --quiet mailinabox-tcp-ipoptions && nft list table inet mailinabox_tcp_ipoptions | grep -q "ip hdrlength > 5"'
fi
check "php imap extension (CLI and FPM)" 'php$PHP_VER -m | grep -qx imap && php-fpm$PHP_VER -m | grep -qx imap'
check "php imap_open against Dovecot" '[ "$(php$PHP_VER -r "\$m = imap_open(\"{127.0.0.1:993/imap/ssl/novalidate-cert}INBOX\", \"$U\", \"$P\"); echo \$m ? \"ok\" : imap_last_error();" 2>&1)" == ok ]'
check "unattended-upgrades installs updates from the PHP repository" 'o=$(sed -n "s/.*\"origin=\([^,]*\),.*/\1/p" /etc/apt/apt.conf.d/51mailinabox-php-updates); c=$(lsb_release -cs); [ -n "$o" ] && apt-cache policy | grep -q "o=$o,a=[^,]*,n=$c," && unattended-upgrade --dry-run --debug 2>&1 | grep -m1 "Allowed origins are:" | grep -q "origin=$o,codename=$c"'
check "php CLI is $PHP_VER" '[ "$(php -r "echo PHP_MAJOR_VERSION.\".\".PHP_MINOR_VERSION;")" == "$PHP_VER" ]'
check "only php$PHP_VER-fpm running" '[ -z "$(systemctl list-units --type=service --state=running --no-legend | grep -o "php[0-9.]*-fpm" | grep -v php$PHP_VER-fpm)" ]'

echo "== control panel"
check "admin page" '$C -o /dev/null -w "%{http_code}" $H/admin/ | grep -q 200'
check "admin assets (jquery, panel css/js, Inter, fontawesome)" '(for a in jquery.min.js panel/panel.css panel/panel.js panel/inter.woff2 fontawesome/css/solid.min.css; do $C -o /dev/null -w "%{http_code}" $H/admin/assets/$a | grep -q 200 || exit 1; done)'
check "admin page doesn't load Bootstrap" '! $C $H/admin/ | grep -qiE "bootstrap(\.bundle)?(\.min)?\.(css|js)|data-bs-" && $C $H/admin/ | grep -q "panel/panel.css?v="'
KEY=$($C -X POST -u "$U:$P" $H/admin/login | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('api_key',''))" 2>/dev/null)
check "admin login via API" '[ -n "$KEY" ]'
check "API: users list" '$C -u "$U:$KEY" "$H/admin/mail/users?format=json" | grep -q "$U"'
check "API: system status" '$C -u "$U:$KEY" -X POST $H/admin/system/status | grep -q "\"type\""'
check "API: dns zonefile" '$C -u "$U:$KEY" $H/admin/dns/zonefile/example.com | grep -q "SOA"'
check "API: backup config" '$C -u "$U:$KEY" $H/admin/system/backup/config | grep -q "min_age_in_days"'
check "API: pgp keyring" '$C -u "$U:$KEY" $H/admin/system/pgp/ | grep -q "daemon"'
check "API: ssl status" '$C -u "$U:$KEY" $H/admin/ssl/status | grep -q "can_provision"'
MC=$(mktemp)
check "API: 2FA QR code is a PNG, drawn without Pillow" '$C -u "$U:$KEY" -X POST $H/admin/mfa/status | python3 -c "import base64, json, sys; png = base64.b64decode(json.load(sys.stdin)[\"new_mfa\"][\"totp\"][\"qr_code_base64\"]); sys.exit(0 if png[:8] == b\"\\x89PNG\\r\\n\\x1a\\n\" else 1)" && ! /usr/local/lib/mailinabox/env/bin/python -c "import PIL" 2>/dev/null'
check "API: munin" '$C -c $MC -u "$U:$KEY" $H/admin/munin/ | grep -q OK && $C -b $MC -o /dev/null -w "%{http_code}" $H/admin/munin/index.html | grep -q 200'

echo "== webmail"
check "roundcube login page" '$C $H/mail/ | grep -qi "rcmloginuser"'
check "roundcube static files (static.php)" '$C -o /dev/null -w "%{http_code}" "$H/mail/static.php/skins/elastic/styles/styles.min.css" | grep -q 200 && $C -o /dev/null -w "%{http_code}" "$H/mail/static.php/plugins/carddav/carddav.js" | grep -q 200'
check "roundcube internals not served" '$C -o /dev/null -w "%{http_code}" "$H/mail/config/config.inc.php" | grep -qv 200 && $C -o /dev/null -w "%{http_code}" "$H/mail/installer.php" | grep -q 403'
check "roundcube no PHP errors" '! grep -qi "fatal\|PHP Parse" /var/log/roundcubemail/errors.log'
RC=$(mktemp)
TOKEN=$($C -c $RC -b $RC $H/mail/ | grep -o 'name="_token" value="[^"]*"' | head -1 | sed 's/.*value="//;s/"//')
LOGIN=$($C -c $RC -b $RC -o /dev/null -w "%{http_code} %{redirect_url}" -X POST $H/mail/?_task=login --data-urlencode "_token=$TOKEN" --data "_task=login&_action=login&_timezone=UTC" --data-urlencode "_user=$U" --data-urlencode "_pass=$P")
check "roundcube IMAP login" 'echo "$LOGIN" | grep -q "302.*_task=mail"'
check "roundcube mailbox list" '$C -c $RC -b $RC "$H/mail/?_task=mail&_mbox=INBOX" | grep -q "Sent"'

echo "== nextcloud"
check "nextcloud status ($NC_MAJOR, installed)" '$C $H/cloud/status.php | grep -q "\"installed\":true" && $C $H/cloud/status.php | grep -q "\"version\":\"$NC_MAJOR\."'
check "nextcloud IMAP login (user_external) + CardDAV" '$C -u "$U:$P" -X PROPFIND -H "Depth: 0" -o /dev/null -w "%{http_code}" $H/cloud/remote.php/dav/addressbooks/users/$U/contacts/ | grep -q 207'
check "nextcloud CalDAV" '$C -u "$U:$P" -X PROPFIND -H "Depth: 0" -o /dev/null -w "%{http_code}" $H/cloud/remote.php/dav/calendars/$U/personal/ | grep -q 207'
check "nextcloud apps enabled" 'sudo -u www-data php /usr/local/lib/owncloud/occ app:list --enabled 2>/dev/null | grep -q "user_external" && sudo -u www-data php /usr/local/lib/owncloud/occ app:list --enabled 2>/dev/null | grep -q "calendar" && sudo -u www-data php /usr/local/lib/owncloud/occ app:list --enabled 2>/dev/null | grep -q "contacts"'
check "nextcloud apps at the setup's versions ($(echo $NC_APPS))" 'sudo -u www-data php$PHP_VER /usr/local/lib/owncloud/occ app:list --enabled --output=json 2>/dev/null | python3 -c "import json, sys; enabled = json.load(sys.stdin)[\"enabled\"]; wrong = {a: enabled.get(a) for a, v in (x.split(\"=\") for x in sys.argv[1:]) if enabled.get(a) != v}; wrong and print(\"installed:\", wrong); sys.exit(1 if wrong else 0)" $NC_APPS'
check "nextcloud config read-only" 'grep -q "config_is_read_only. => true" /home/user-data/owncloud/config.php'

echo "== z-push"
check "activesync OPTIONS" '$C -u "$U:$P" -X OPTIONS -D - -o /dev/null $H/Microsoft-Server-ActiveSync | grep -qi "MS-ASProtocolVersions"'
check "autodiscover" '$C -X POST -H "Content-Type: text/xml" -u "$U:$P" --data "<?xml version=\"1.0\" encoding=\"utf-8\"?><Autodiscover xmlns=\"http://schemas.microsoft.com/exchange/autodiscover/mobilesync/requestschema/2006\"><Request><EMailAddress>$U</EMailAddress><AcceptableResponseSchema>http://schemas.microsoft.com/exchange/autodiscover/mobilesync/responseschema/2006</AcceptableResponseSchema></Request></Autodiscover>" $H/autodiscover/autodiscover.xml | grep -qi "Microsoft-Server-ActiveSync"'
check "z-push $ZPUSH_VER installed" '[ "$(cat /usr/local/lib/z-push/version)" == "$ZPUSH_VER" ]'
check "z-push-admin" 'z-push-admin -a list 2>&1 | grep -qi "device\|All synchronized\|no devices"'
printf '\x03\x01\x6a\x00\x00\x07\x56\x52\x030\x00\x01\x01' > /tmp/foldersync.wbxml
check "activesync FolderSync (WBXML, IMAP backend)" '$C -u "$U:$P" -X POST -H "MS-ASProtocolVersion: 14.0" -H "Content-Type: application/vnd.ms-sync.wbxml" -H "User-Agent: Apple-iPhone/1" --data-binary @/tmp/foldersync.wbxml -o /tmp/foldersync.out -w "%{http_code} %{content_type}" "$H/Microsoft-Server-ActiveSync?Cmd=FolderSync&User=$U&DeviceId=TESTDEVICE1&DeviceType=iPhone" | grep -q "^200 application/vnd.ms-sync.wbxml" && [ -s /tmp/foldersync.out ]'
check "activesync device registered" 'z-push-admin -a list 2>&1 | grep -qi TESTDEVICE1'

echo "== mail flow"
python3 - "$U" "$P" <<'EOF' && ok "SMTP submission (587, STARTTLS) + delivery" || bad "SMTP submission (587, STARTTLS) + delivery"
import smtplib, ssl, sys, time, imaplib, uuid
u, p = sys.argv[1], sys.argv[2]
tag = uuid.uuid4().hex
ctx = ssl._create_unverified_context()
s = smtplib.SMTP("127.0.0.1", 587, timeout=60)
s.starttls(context=ctx); s.login(u, p)
s.sendmail(u, [u], "From: %s\r\nTo: %s\r\nSubject: test %s\r\n\r\nhello\r\n" % (u, u, tag))
s.quit()
for i in range(60):
    m = imaplib.IMAP4_SSL("127.0.0.1", 993, ssl_context=ctx)
    m.login(u, p); m.select("INBOX")
    typ, d = m.search(None, "SUBJECT", tag)
    m.logout()
    if d[0]:
        sys.exit(0)
    time.sleep(2)
sys.exit(1)
EOF
python3 - "$U" "$P" <<'EOF' && ok "SMTPS (465) login" || bad "SMTPS (465) login"
import smtplib, ssl, sys
ctx = ssl._create_unverified_context()
s = smtplib.SMTP_SSL("127.0.0.1", 465, context=ctx, timeout=30); s.login(sys.argv[1], sys.argv[2]); s.quit()
EOF
python3 - "$U" "$P" <<'EOF' && ok "POP3S login" || bad "POP3S login"
import poplib, ssl, sys
ctx = ssl._create_unverified_context()
m = poplib.POP3_SSL("127.0.0.1", 995, context=ctx, timeout=30); m.user(sys.argv[1]); m.pass_(sys.argv[2]); print(m.stat()); m.quit()
EOF
python3 - "$U" "$P" <<'EOF' && ok "spam learning on move to Spam (sa-learn)" || bad "spam learning on move to Spam (sa-learn)"
import imaplib, ssl, sys, time, subprocess
ctx = ssl._create_unverified_context()
def nspam():
    out = subprocess.run(["sudo", "-u", "spampd", "sa-learn", "--dump", "magic", "--dbpath", "/home/user-data/mail/spamassassin"], capture_output=True, text=True).stdout
    for l in out.splitlines():
        if "nspam" in l: return int(l.split()[2])
    return 0
before = nspam()
m = imaplib.IMAP4_SSL("127.0.0.1", 993, ssl_context=ctx)
m.login(sys.argv[1], sys.argv[2]); m.select("INBOX")
typ, d = m.uid("SEARCH", None, "ALL")
uid = d[0].split()[-1]
m.uid("COPY", uid, "Spam")
m.logout()
for i in range(20):
    if nspam() > before: sys.exit(0)
    time.sleep(1)
print("nspam", before, nspam()); sys.exit(1)
EOF

echo "== quota"
check "quota (maildirsize) via API" '$C -u "$U:$KEY" -X POST -d "email=$U&quota=100M" $H/admin/mail/users/quota | grep -qi "ok\|updated" && doveadm quota get -u $U 2>/dev/null | grep -q "STORAGE.*102400"'

echo "== backup"
check "backup (duplicity) runs" '/root/mailinabox/management/backup.py > /tmp/backup.log 2>&1 && ls /home/user-data/backup/encrypted | grep -q "duplicity-full"' || cat /tmp/backup.log | tail -5
check "backup status via API" '$C -u "$U:$KEY" $H/admin/system/backup/status | grep -q "\"full\": true\|\"full\":true"'

echo "== login protection"
check "port 25 doesn't offer AUTH, 587 does" '[ "$(postconf -h smtpd_sasl_auth_enable)" = no ] && python3 -c "
import smtplib, ssl
ctx = ssl._create_unverified_context()
def auth(port):
    s = smtplib.SMTP(\"127.0.0.1\", port, timeout=20); s.ehlo(); s.starttls(context=ctx); s.ehlo()
    a = s.has_extn(\"auth\"); s.quit(); return a
raise SystemExit(0 if not auth(25) and auth(587) else 1)"'
check "AUTH rate and error limits on 465 and 587" '(for s in smtps submission; do postconf -P "$s/inet/smtpd_client_auth_rate_limit" | grep -q "= 10$" && postconf -P "$s/inet/smtpd_hard_error_limit" | grep -q "= 10$" || exit 1; done)'
check "fail2ban: slow jail for the submission ports" 'fail2ban-client status miab-postfix-slow | grep -q "Currently banned" && [ "$(fail2ban-client get miab-postfix-slow findtime)" = 86400 ] && [ "$(fail2ban-client get miab-postfix-slow maxretry)" = 10 ]'
F2B_SLOW_FILTER=/etc/fail2ban/filter.d/miab-postfix-submission-slow.conf
F2B_LOG=$(mktemp)
cat > $F2B_LOG <<-'EOF'
	Nov  9 10:00:01 box postfix/submission/smtpd[1]: warning: unknown[203.0.113.7]: SASL LOGIN authentication failed: UGFzc3dvcmQ6
	Nov  9 10:00:02 box postfix/submission/smtpd[1]: warning: unknown[203.0.113.8]: SASL PLAIN authentication failed: Connection lost to authentication server
	Nov  9 10:00:03 box postfix/submission/smtpd[1]: warning: unknown[203.0.113.9]: SASL LOGIN authentication aborted
	Nov  9 10:00:04 box postfix/smtpd[1]: warning: unknown[203.0.113.10]: SASL LOGIN authentication failed: UGFzc3dvcmQ6
EOF
check "fail2ban: slow filter counts real failures only" 'fail2ban-regex $F2B_LOG $F2B_SLOW_FILTER 2>/dev/null | grep -qE "^Lines: 4 lines, [0-9]+ ignored, 1 matched"'
check "login alerts: new network reported, then trusted by fail2ban and the blocklists" 'echo "$(date "+%b %d %H:%M:%S") imap-login: Info: Login: user=<$U>, method=PLAIN, rip=198.51.100.23, lip=10.0.0.1, mpid=1, TLS, session=<x>" >> /var/log/mail.log && /root/mailinabox/management/login_alerts.py | grep -q "$U from 198.51.100.23 (block 198.51.0.0/16)" && [ -z "$(/root/mailinabox/management/login_alerts.py)" ] && fail2ban-client get miab-postfix-slow ignoreip | grep -q "198.51.100.0/24"'
check "login alerts: a new network in a known operator block isn't reported, but is trusted" 'echo "$(date "+%b %d %H:%M:%S") imap-login: Info: Login: user=<$U>, method=PLAIN, rip=198.51.101.5, lip=10.0.0.1, mpid=1, TLS, session=<y>" >> /var/log/mail.log && [ -z "$(/root/mailinabox/management/login_alerts.py)" ] && fail2ban-client get miab-postfix-slow ignoreip | grep -q "198.51.101.0/24"'
check "login alerts: first and last times come from the log, not from when it runs" 'echo "$(LC_ALL=C date -d "3 days ago" "+%b %d") 12:00:00 imap-login: Info: Login: user=<$U>, method=PLAIN, rip=203.0.113.9, lip=10.0.0.1, mpid=1, TLS, session=<z>" >> /var/log/mail.log && /root/mailinabox/management/login_alerts.py > /dev/null && /root/mailinabox/management/login_alerts.py status | python3 -c "import json, sys; want = sys.argv[1]; r = [x for x in json.load(sys.stdin)[\"recent\"] if x[\"address\"] == \"203.0.113.9\"]; sys.exit(0 if r and r[0][\"first\"][:10] == want and r[0][\"last\"][:10] == want else 1)" "$(date -u -d "$(date -d "3 days ago" +%F) 12:00" +%F)"'
if [ -f /etc/systemd/system/mailinabox-ip-blocklists.service ]; then
	check "IP blocklists loaded (on by default), with known logins exempt" 'systemctl is-enabled --quiet mailinabox-ip-blocklists && nft list set inet mailinabox_ip_blocklists block4 | grep -q "/" && nft get element inet mailinabox_ip_blocklists allow4 "{ 198.51.100.23 }" >/dev/null 2>&1 && nft get element inet mailinabox_ip_blocklists allow4 "{ 198.51.101.5 }" >/dev/null 2>&1 && nft list chain inet mailinabox_ip_blocklists input | grep -q "465, 587"'
	check "API: login protection status" '$C -u "$U:$KEY" $H/admin/mail/login-protection | python3 -c "import json, sys; d = json.load(sys.stdin); b = d[\"blocklists\"]; sys.exit(0 if b[\"enabled\"] and b[\"networks4\"] > 0 and any(s.get(\"ok\") for s in b[\"sources\"]) and d[\"login_alerts\"][\"enabled\"] and d[\"login_alerts\"][\"locate\"] and all(\"block\" in r for r in d[\"login_alerts\"][\"recent\"]) else 1)"'
	BL=$(mktemp -d)
	printf "# test list\n192.0.2.1\n" > $BL/empty.txt  # only a documentation address: no valid entries
	(cd $BL && timeout 120 python3 -m http.server 8765 --bind 127.0.0.1 >/dev/null 2>&1 &)
	sleep 1
	check "API: a list without valid entries adds nothing and is reported" '$C -u "$U:$KEY" -X POST -d "action=add_source&url=http://127.0.0.1:8765/empty.txt" $H/admin/mail/login-protection | grep -q Blocking && $C -u "$U:$KEY" $H/admin/mail/login-protection | grep -q "no valid IP addresses" && nft list set inet mailinabox_ip_blocklists block4 | grep -q "/"'
	check "API: removing a list and turning the blocklists off and on" '$C -u "$U:$KEY" -X POST -d "action=remove_source&url=http://127.0.0.1:8765/empty.txt" $H/admin/mail/login-protection | grep -q Blocking && $C -u "$U:$KEY" -X POST -d "action=blocklists&enabled=0" $H/admin/mail/login-protection | grep -q "off" && ! nft list table inet mailinabox_ip_blocklists >/dev/null 2>&1 && $C -u "$U:$KEY" -X POST -d "action=blocklists&enabled=1" $H/admin/mail/login-protection | grep -q Blocking && nft list table inet mailinabox_ip_blocklists >/dev/null 2>&1'
fi

echo "== security dashboard"
check "security metrics: hourly sample" '/root/mailinabox/management/security_metrics.py collect && python3 -c "import json; s = json.load(open(\"/var/lib/mailinabox/security-metrics.json\")); assert \"samples\" in s"'
echo "$(LC_ALL=C date "+%b %e %H:%M:%S") box postfix/submission/smtpd[1]: warning: unknown[198.51.100.77]: SASL LOGIN authentication failed: UGFzc3dvcmQ6" >> /var/log/mail.log
check "API: security metrics count a failed login and its address" '$C -u "$U:$KEY" "$H/admin/system/security-metrics?range=24h" | python3 -c "import json, sys; d = json.load(sys.stdin); sys.exit(0 if len(d[\"buckets\"]) == 24 and d[\"totals\"][\"failed\"] >= 1 and any(t[\"address\"] == \"198.51.100.77\" for t in d[\"top\"]) else 1)"'
check "API: security metrics for 7 and 30 days" '$C -u "$U:$KEY" "$H/admin/system/security-metrics?range=7d" | grep -q "\"bucket_seconds\": 86400" && $C -u "$U:$KEY" "$H/admin/system/security-metrics?range=30d" | python3 -c "import json, sys; sys.exit(0 if len(json.load(sys.stdin)[\"buckets\"]) == 30 else 1)"'
check "dashboard loads the charts" '$C $H/admin/ | grep -q "panel/charts.js?v=" && $C -o /dev/null -w "%{http_code}" $H/admin/assets/panel/charts.js | grep -q 200'

echo "== status checks"
/root/mailinabox/management/status_checks.py > /tmp/status.txt 2>&1
check "status checks run" 'grep -q "System" /tmp/status.txt && ! grep -q "Traceback" /tmp/status.txt'
grep -E "^\s*✖|Traceback" /tmp/status.txt | head -20

echo "RESULT: $pass passed, $fail failed"
