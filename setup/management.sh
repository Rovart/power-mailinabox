#!/bin/bash

source setup/functions.sh
source /etc/mailinabox.conf # load global vars

echo "Installing Mail-in-a-Box system management daemon..."

# DEPENDENCIES

# virtualenv is used to isolate the Python 3 packages we
# install via pip from the system-installed packages.
#
# certbot installs EFF's certbot which we use to
# provision free TLS certificates.
#
# gcc and build tools are required to install the latest version
# of duplicity
apt_install python3-pip python3-gpg python3-venv virtualenv certbot rsync librsync-dev \
			gcc python3-dev gettext

apt_get_quiet remove --autoremove --purge duplicity || /bin/true

# Previous versions installed duplicity, b2sdk, boto3 (and email_validator) system-wide
# with pip, which also replaced some of the distribution's Python packages with
# incompatible versions (breaking certbot, for instance). Remove them.
LEGACY_PIP_PACKAGES=$(tools/legacy_pip_packages.py)
if [ -n "$LEGACY_PIP_PACKAGES" ]; then
	echo "Removing Python packages installed system-wide by a previous version..."
	PIP_BREAK_SYSTEM_PACKAGES=1 hide_output python3 -m pip uninstall -y $LEGACY_PIP_PACKAGES
fi

# Duplicity does the actual backups.
# b2sdk is used for backblaze backups.
# boto3 is used for amazon aws backups.
#
# We install the latest version of duplicity and its backends in a virtualenv of their
# own (modern distributions don't allow installing packages system-wide with pip, see
# PEP 668), and link the duplicity command to where the management daemon expects it.
inst_dir=/usr/local/lib/mailinabox
mkdir -p $inst_dir
duplicity_venv=$inst_dir/duplicity
if [ ! -f $duplicity_venv/.oscode ] || [ "$(cat $duplicity_venv/.oscode)" != $(get_os_code) ]; then
	rm -rf $duplicity_venv
	hide_output python3 -m venv $duplicity_venv
fi
hide_output $duplicity_venv/bin/pip install --upgrade pip
hide_output $duplicity_venv/bin/pip install --upgrade duplicity b2sdk boto3
echo $(get_os_code) > $duplicity_venv/.oscode
ln -sf $duplicity_venv/bin/duplicity /usr/local/bin/duplicity

# Create a virtualenv for the installation of Python 3 packages
# used by the management daemon.
venv=$inst_dir/env
if [ ! -d $venv ]; then
	hide_output virtualenv -ppython3 $venv
elif [ ! -f $venv/.oscode ]; then
	echo "Re-creating Python environment..."
	rm -rf $venv
	hide_output virtualenv -ppython3 $venv
elif [ "$(cat $venv/.oscode)" != $(get_os_code) ]; then
	echo "Existing management environment is from an earlier version of the OS you're running."
	echo "Re-creating Python environment..."
	rm -rf $venv
	hide_output virtualenv -ppython3 $venv
fi

# Upgrade pip because the Ubuntu-packaged version is out of date.
hide_output $venv/bin/pip install --upgrade pip

# Install other Python 3 packages used by the management daemon.
# The first line is the packages that Josh maintains himself!
# NOTE: email_validator is also used in setup/questions.sh (from the distribution's packages).
# --upgrade-strategy eager also upgrades their dependencies: with the default only-if-needed,
# an existing venv keeps old Jinja2, Werkzeug, urllib3 or aiohttp (behind the admin panel and
# the MTA-STS resolver) on every setup run, whatever security fixes they got.
hide_output $venv/bin/pip install --upgrade --upgrade-strategy eager \
	rtyaml "email_validator>=1.0.0" "exclusiveprocess" \
	flask dnspython python-dateutil expiringdict gunicorn \
	qrcode[pil] pyotp \
	"idna>=2.0.0" cryptography psutil postfix-mta-sts-resolver boto3 b2sdk

# Make the venv use the packaged gpgme bindings (the ones pip provides are severely out-of-date)
if [ ! -d $venv/lib/python$(python_version)/site-packages/gpg/ ]; then
	ln -s /usr/lib/python3/dist-packages/gpg/ $venv/lib/python$(python_version)/site-packages/
fi

echo $(get_os_code) > $venv/.oscode

# CONFIGURATION

# Create a backup directory and a random key for encrypting backups.
mkdir -p $STORAGE_ROOT/backup
if [ ! -f $STORAGE_ROOT/backup/secret_key.txt ]; then
	(umask 077; openssl rand -base64 2048 > $STORAGE_ROOT/backup/secret_key.txt)
fi


# Download jQuery and Bootstrap local files

# Make sure we have the directory to save to.
assets_dir=$inst_dir/vendor/assets
rm -rf $assets_dir
mkdir -p $assets_dir

# jQuery CDN URL
jquery_version=4.0.0
jquery_url=https://code.jquery.com # Check this link for new versions

# Get jQuery
wget_verify $jquery_url/jquery-$jquery_version.min.js b19b077cdc2cf89c66a644f0305ef4959f8472bc $assets_dir/jquery.min.js

# Bootstrap CDN URL
# See https://github.com/twbs/bootstrap/releases to check for new versions
bootstrap_version=5.3.8
bootstrap_url=https://github.com/twbs/bootstrap/releases/download/v$bootstrap_version/bootstrap-$bootstrap_version-dist.zip

# Get Bootstrap
wget_verify $bootstrap_url c3c6d623ef78a8ccdd688765421ac20ee36126c5 /tmp/bootstrap.zip
unzip -q /tmp/bootstrap.zip -d $assets_dir
mv $assets_dir/bootstrap-$bootstrap_version-dist $assets_dir/bootstrap
rm -f /tmp/bootstrap.zip

# FontAwesome CDN URL
# See https://github.com/FortAwesome/Font-Awesome/releases to check for new versions
fontawesome_version=7.3.1
fontawesome_url=https://github.com/FortAwesome/Font-Awesome/releases/download/$fontawesome_version/fontawesome-free-$fontawesome_version-web.zip

# Get FontAwesome
wget_verify $fontawesome_url c997775ace7090f4fce41d881edce25727927bed /tmp/fontawesome.zip
unzip -q /tmp/fontawesome.zip -d $assets_dir
mv $assets_dir/fontawesome-free-$fontawesome_version-web $assets_dir/fontawesome
rm -f /tmp/fontawesome.zip

# Create an init script to start the management daemon and keep it
# running after a reboot.
# Set a long timeout since some commands take a while to run, matching
# the timeout we set for PHP (fastcgi_read_timeout in the nginx confs).
# Note: Authentication currently breaks with more than 1 gunicorn worker.
cat > $inst_dir/start <<EOF;
#!/bin/bash
# Set character encoding flags to ensure that any non-ASCII don't cause problems.
export LANGUAGE=en_US.UTF-8
export LC_ALL=en_US.UTF-8
export LANG=en_US.UTF-8
export LC_TYPE=en_US.UTF-8

mkdir -p /var/lib/mailinabox
tr -cd '[:xdigit:]' < /dev/urandom | head -c 32 > /var/lib/mailinabox/api.key
chmod 640 /var/lib/mailinabox/api.key

source $venv/bin/activate
export PYTHONPATH=$(pwd)/management
exec gunicorn -b 127.0.0.1:10222 -w 1 --timeout 630 wsgi:app
EOF
chmod +x $inst_dir/start
install_systemd_unit mailinabox.service conf/mailinabox.service
hide_output systemctl enable mailinabox.service

# Perform nightly tasks at 3am in system time: take a backup, run
# status checks and email the administrator any changes.

minute=$((RANDOM % 60))  # avoid overloading mailinabox.email
cat > /etc/cron.d/mailinabox-nightly << EOF;
# Mail-in-a-Box --- Do not edit / will be overwritten on update.
# Run nightly tasks: backup, status checks.
$minute 3 * * *	root	(cd $(pwd) && management/daily_tasks.sh)
EOF

# Start the management server.
restart_service mailinabox

# FOR DEVELOPMENT PURPOSES ONLY:
# If there is a CA certificate in the folder, install it.
# MIAB will only accept a manual certificate installation
# if it is signed by a CA trusted by it.
if [[ -f mailinabox-ca.crt ]]; then
    echo "Custom CA certificate detected. Installing..."
    rm -f /usr/local/share/ca-certificates/mailinabox-ca.crt
    cp mailinabox-ca.crt /usr/local/share/ca-certificates/
    hide_output update-ca-certificates --fresh
fi
