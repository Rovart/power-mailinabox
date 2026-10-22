#!/bin/bash
# Tests turning the optional services off and on (run inside the container).
# Expected versions, from the setup scripts being tested.
PHP_VER=$(sed -n 's/^PHP_VER=//p' /root/mailinabox/setup/functions.sh)
U=me@example.com
P='Secret123!pw'
H=https://127.0.0.1
C="curl -sk --max-time 60"
CLI="/root/mailinabox/management/cli.py"
pass=0; fail=0
ok()  { echo "PASS: $1"; pass=$((pass+1)); }
bad() { echo "FAIL: $1"; fail=$((fail+1)); }
check() { if eval "$2"; then ok "$1"; else bad "$1"; fi; }
code() { $C -o /dev/null -w "%{http_code}" "$@"; }
KEY=$($C -X POST -u "$U:$P" $H/admin/login | python3 -c "import json,sys; print(json.load(sys.stdin).get('api_key',''))")
printf '\x03\x01\x6a\x00\x00\x07\x56\x52\x030\x00\x01\x01' > /tmp/foldersync.wbxml
foldersync() { $C -u "$U:$P" -X POST -H "MS-ASProtocolVersion: 14.0" -H "Content-Type: application/vnd.ms-sync.wbxml" -H "User-Agent: Apple-iPhone/1" --data-binary @/tmp/foldersync.wbxml -o /dev/null -w "%{http_code}" "$H/Microsoft-Server-ActiveSync?Cmd=FolderSync&User=$U&DeviceId=TESTDEVICE2&DeviceType=iPhone"; }
rc_plugins() { php$PHP_VER -r 'include "/usr/local/lib/roundcubemail/config/config.inc.php"; echo implode(",", $config["plugins"]);'; }

echo "== all on"
check "CLI lists 4 services on" '[ $($CLI system services | grep -c " on ") == 4 ]'
check "API lists services" '$C -u "$U:$KEY" $H/admin/system/services | grep -q "\"name\": \"nextcloud\""'
check "services.json all on" 'python3 -c "import json,sys; d=json.load(open(\"/var/lib/mailinabox/services.json\")); sys.exit(0 if all(d.values()) and len(d)==4 else 1)"'
check "mobileconfig has CalDAV+CardDAV" 'grep -q com.apple.caldav.account /var/lib/mailinabox/mobileconfig.xml && grep -q com.apple.carddav.account /var/lib/mailinabox/mobileconfig.xml'
check "mobileconfig served and valid" '$C $H/mailinabox.mobileconfig | python3 -c "import plistlib,sys; d=plistlib.loads(sys.stdin.buffer.read()); sys.exit(0 if len(d[\"PayloadContent\"])==3 else 1)"'
check "roundcube has carddav plugin" 'rc_plugins | grep -q carddav'

echo "== nextcloud off"
check "disable nextcloud (CLI)" '$CLI system services disable nextcloud | grep -q "turned off"'
sleep 2
check "nextcloud not served" '[ $(code $H/cloud/status.php) == 404 ] && [ $(code $H/cloud/) == 404 ]'
check ".well-known/caldav not served" '[ $(code $H/.well-known/caldav) == 404 ]'
check "nextcloud cron removed" '[ ! -e /etc/cron.d/mailinabox-nextcloud ] && [ -e /var/lib/mailinabox/cron.disabled/mailinabox-nextcloud ]'
check "mobileconfig without CalDAV/CardDAV" '$C $H/mailinabox.mobileconfig | python3 -c "import plistlib,sys; d=plistlib.loads(sys.stdin.buffer.read()); sys.exit(0 if [p[\"PayloadType\"] for p in d[\"PayloadContent\"]]==[\"com.apple.mail.managed\"] else 1)"'
check "roundcube without carddav plugin" '! rc_plugins | grep -q carddav'
check "webmail still works" '$C $H/mail/ | grep -qi rcmloginuser'
check "activesync FolderSync with IMAP only" '[ $(foldersync) == 200 ]'
check "settings.yaml stores the choice" 'grep -A4 "^services:" /home/user-data/settings.yaml | grep -q "nextcloud: false"'

echo "== nextcloud on"
check "enable nextcloud (API)" '$C -u "$U:$KEY" -X POST -d "service=nextcloud&enabled=true" $H/admin/system/services | grep -q "turned on"'
sleep 2
check "nextcloud served again" '$C $H/cloud/status.php | grep -q "\"installed\":true"'
check "nextcloud cron back" '[ -e /etc/cron.d/mailinabox-nextcloud ] && [ ! -e /var/lib/mailinabox/cron.disabled/mailinabox-nextcloud ]'
check "nextcloud CalDAV works" '[ $($C -u "$U:$P" -X PROPFIND -H "Depth: 0" -o /dev/null -w "%{http_code}" $H/cloud/remote.php/dav/calendars/$U/personal/) == 207 ]'
check "roundcube has carddav again" 'rc_plugins | grep -q carddav'

echo "== z-push off/on"
check "disable zpush" '$CLI system services disable zpush | grep -q "turned off"'
sleep 2
check "activesync not served" '[[ $(foldersync) =~ ^40[45]$ ]]'
check "autodiscover not served" '[[ $(code -X POST $H/autodiscover/autodiscover.xml) =~ ^40[45]$ ]]'
check "enable zpush" '$CLI system services enable zpush | grep -q "turned on"'
sleep 2
check "activesync served again" '[ $(foldersync) == 200 ]'

echo "== webmail off/on"
check "disable webmail" '$CLI system services disable webmail | grep -q "turned off"'
sleep 2
check "webmail not served" '[ $(code $H/mail/) == 404 ] && [ $(code $H/mail/index.php) == 404 ]'
check "thunderbird autoconfig still served" '$C $H/mail/config-v1.1.xml | grep -q emailProvider'
check "enable webmail" '$CLI system services enable webmail | grep -q "turned on"'
sleep 2
check "webmail served again" '$C $H/mail/ | grep -qi rcmloginuser'

echo "== munin off/on"
check "disable munin" '$CLI system services disable munin | grep -q "turned off"'
sleep 2
check "munin-node stopped and disabled" '! systemctl is-active --quiet munin-node && ! systemctl is-enabled --quiet munin-node'
check "munin cron removed" '[ ! -e /etc/cron.d/munin ]'
check "munin API 404" '[ $(code -u "$U:$KEY" $H/admin/munin/) == 404 ]'
check "enable munin" '$CLI system services enable munin | grep -q "turned on"'
sleep 2
check "munin-node running again" 'systemctl is-active --quiet munin-node && systemctl is-enabled --quiet munin-node && [ -e /etc/cron.d/munin ]'
MC=$(mktemp)
check "munin served again" '$C -c $MC -u "$U:$KEY" $H/admin/munin/ | grep -q OK && [ $($C -b $MC -o /dev/null -w "%{http_code}" $H/admin/munin/index.html) == 200 ]'

echo "== errors"
check "unknown service rejected" '[ $(code -u "$U:$KEY" -X POST -d "service=bogus&enabled=false" $H/admin/system/services) == 400 ]'
check "non-admin cannot toggle" '[ $(code -X POST -d "service=nextcloud&enabled=false" $H/admin/system/services) == 401 ] || [ $(code -X POST -d "service=nextcloud&enabled=false" $H/admin/system/services) == 403 ]'
check "apply is idempotent" '/root/mailinabox/management/services.py apply > /dev/null && /root/mailinabox/management/services.py apply | grep -q "on"'

echo "== security headers"
check "CSP on /admin/" '$C -D - -o /dev/null $H/admin/ | grep -qi "content-security-policy: default-src .self."'
check "nginx config valid" 'nginx -t 2>/dev/null'

echo "RESULT: $pass passed, $fail failed"
