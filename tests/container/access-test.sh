#!/bin/bash
# Tests restricting web applications to local access. Runs on the host: requests
# from the host are "external", requests made inside the container are local.
# usage: access-test.sh <container>
N=$1
IP=$(docker exec $N hostname -I | awk '{print $1}')
U=me@example.com; P='Secret123!pw'
CLI="/root/mailinabox/management/cli.py"
pass=0; fail=0
ok()  { echo "PASS: $1"; pass=$((pass+1)); }
bad() { echo "FAIL: $1"; fail=$((fail+1)); }
check() { if eval "$2"; then ok "$1"; else bad "$1"; fi; }
ext() { curl -sk --noproxy "*" --max-time 30 -o /dev/null -w "%{http_code}" "$@"; }
loc() { docker exec $N curl -sk --max-time 30 -o /dev/null -w "%{http_code}" "$@"; }
box() { docker exec $N bash -c "$1"; }

echo "== all public"
check "CLI lists 4 public areas" '[ $(box "$CLI system web-access" | grep -c " public ") == 4 ]'
check "admin reachable externally" '[ $(ext https://$IP/admin/) == 200 ]'

echo "== everything local only"
check "restrict all (CLI)" 'box "$CLI system web-access all local" | grep -q "only available locally"'
sleep 2
check "admin 403 externally" '[ $(ext https://$IP/admin/) == 403 ] && [ $(ext https://$IP/admin/assets/jquery.min.js) == 403 ] && [ $(ext -X POST https://$IP/admin/login) == 403 ]'
check "munin 403 externally" '[ $(ext https://$IP/admin/munin/index.html) == 403 ]'
check "webmail 403 externally" '[ $(ext https://$IP/mail/) == 403 ] && [ $(ext https://$IP/mail/index.php) == 403 ]'
check "nextcloud 403 externally" '[ $(ext https://$IP/cloud/status.php) == 403 ] && [ $(ext https://$IP/cloud/) == 403 ]'
check "dav proxy 403 externally" '[ $(ext -u "$U:$P" -X PROPFIND https://$IP/carddav/addressbooks/) == 403 ]'
check "activesync 403 externally" '[ $(ext -u "$U:$P" -X OPTIONS https://$IP/Microsoft-Server-ActiveSync) == 403 ] && [ $(ext -X POST https://$IP/autodiscover/autodiscover.xml) == 403 ]'
check "autoconfig, mobileconfig, mta-sts still public" '[ $(ext https://$IP/.well-known/autoconfig/mail/config-v1.1.xml) == 200 ] && [ $(ext https://$IP/mailinabox.mobileconfig) == 200 ] && [ $(ext https://$IP/.well-known/mta-sts.txt) == 200 ]'
check "admin works locally" '[ $(loc https://127.0.0.1/admin/) == 200 ] && [ $(loc -X POST -u "$U:$P" https://127.0.0.1/admin/login) == 200 ]'
check "admin works through the box address" '[ $(loc https://$IP/admin/) == 200 ]'
check "webmail and nextcloud work locally" '[ $(loc https://127.0.0.1/mail/) == 200 ] && [ $(loc https://127.0.0.1/cloud/status.php) == 200 ]'
check "activesync FolderSync locally (DAV via 127.0.0.1)" 'box "printf \"\\x03\\x01\\x6a\\x00\\x00\\x07\\x56\\x52\\x030\\x00\\x01\\x01\" > /tmp/fs.wbxml; curl -sk -o /dev/null -w %{http_code} -u \"$U:$P\" -X POST -H \"MS-ASProtocolVersion: 14.0\" -H \"Content-Type: application/vnd.ms-sync.wbxml\" --data-binary @/tmp/fs.wbxml \"https://127.0.0.1/Microsoft-Server-ActiveSync?Cmd=FolderSync&User=$U&DeviceId=TESTDEVICE3&DeviceType=iPhone\"" | grep -q 200'
check "nginx config has the rules" 'box "grep -c \"deny all;\" /etc/nginx/conf.d/local.conf" | grep -qv "^0$"'

echo "== allowed networks"
check "invalid network rejected" 'box "$CLI system web-access allow \"10.0.0.0/8 bogus\" 2>&1" | grep -q "not a valid"'
check "allow the host" 'box "$CLI system web-access allow 172.17.0.0/16" | grep -q saved'
sleep 2
check "admin reachable from allowed network" '[ $(ext https://$IP/admin/) == 200 ]'
check "API reports the client as allowed" 'KEY=$(curl -sk -X POST -u "$U:$P" https://$IP/admin/login | python3 -c "import json,sys; print(json.load(sys.stdin)[\"api_key\"])"); curl -sk -u "$U:$KEY" https://$IP/admin/system/web-access | grep -q "\"client_allowed\": true"'
check "clear allowed networks" 'box "$CLI system web-access allow \"\"" | grep -q saved'
sleep 2
check "admin 403 again" '[ $(ext https://$IP/admin/) == 403 ]'

echo "== one area"
check "open admin only" 'box "$CLI system web-access admin public" | grep -q "available from anywhere"'
sleep 2
check "admin public, webmail still local" '[ $(ext https://$IP/admin/) == 200 ] && [ $(ext https://$IP/mail/) == 403 ]'

echo "== undo without the daemon"
box "systemctl stop mailinabox"
check "services.py web-access all public (daemon stopped)" 'box "cd /root/mailinabox && management/services.py web-access all public" | grep -c public | grep -q 4'
box "systemctl start mailinabox"; sleep 3
check "everything public again" '[ $(ext https://$IP/mail/) == 200 ] && [ $(ext --resolve box.example.com:443:$IP https://box.example.com/cloud/status.php) == 200 ] && [ $(ext -u "$U:$P" -X OPTIONS https://$IP/Microsoft-Server-ActiveSync) == 200 ]'
check "setting persisted" 'box "grep -A6 ^web_access: /home/user-data/settings.yaml" | grep -q "admin: false"'

echo "RESULT: $pass passed, $fail failed"
