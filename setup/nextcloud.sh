#!/bin/bash
# Nextcloud
##########################

source setup/functions.sh # load our functions
source /etc/mailinabox.conf # load global vars

# ### Installing Nextcloud

echo "Installing Nextcloud (contacts/calendar)..."

# Nextcloud core and app (plugin) versions to install.
# With each version we store a hash to ensure we install what we expect.

# Nextcloud core
# --------------
# * See https://nextcloud.com/changelog for the latest version.
# * Check https://docs.nextcloud.com/server/latest/admin_manual/installation/system_requirements.html
#   for whether it supports the version of PHP we install (PHP_VER in functions.sh).
# * Since Nextcloud only supports upgrades from consecutive major versions,
#   we automatically install intermediate versions as needed.
# * The hash is the SHA1 hash of the ZIP package, which you can find by just running this script and
#   copying it from the error message when it doesn't match what is below.
nextcloud_ver=34.0.5
nextcloud_hash=dd2998ea28f8951cecab5e0e40eb44652686fa04

# Nextcloud apps
# --------------
# * Find the most recent tag that is compatible with the Nextcloud version above by
#   consulting the <dependencies>...<nextcloud> node at:
#   https://github.com/nextcloud-releases/contacts/blob/main/appinfo/info.xml
#   https://github.com/nextcloud-releases/calendar/blob/main/appinfo/info.xml
#   https://github.com/nextcloud-releases/user_external
#   or by querying https://apps.nextcloud.com/api/v1/platform/<nextcloud version>/apps.json
# * The hash is the SHA1 hash of the release tarball (from the releases/download URL, not the
#   source archive), which you can find by running: curl -sL <url> | sha1sum
contacts_ver=8.9.2
contacts_hash=9539f80acbdf9a9dd3f3a7006939b785c8bafe18
calendar_ver=6.6.4
calendar_hash=587cba617e9557d6963ad651272e673dc0dfd4ba
# user_external is what allows Nextcloud to authenticate users against our IMAP server.
# It is the one holding us back from newer Nextcloud versions: check that a compatible
# release exists before bumping Nextcloud to a new major version!
user_external_ver=4.0.0
user_external_hash=214497dd8691f279ba3740797c565310f0793054

# Clear prior packages and install dependencies from apt.

# Just in case someone tries to cross over from the upstream version.
apt-get purge -qq -y owncloud* 2> /dev/null || /bin/true

apt_install php${PHP_VER} php${PHP_VER}-fpm \
	php${PHP_VER}-cli php${PHP_VER}-sqlite3 php${PHP_VER}-gd php${PHP_VER}-imap php${PHP_VER}-curl curl \
	php${PHP_VER}-xml php${PHP_VER}-mbstring php${PHP_VER}-zip php${PHP_VER}-apcu \
	php${PHP_VER}-intl php${PHP_VER}-imagick php${PHP_VER}-gmp php${PHP_VER}-bcmath php${PHP_VER}-opcache sqlite3

phpenmod -v $PHP_VER apcu

management/editconf.py /etc/php/$PHP_VER/mods-available/apcu.ini -c ';' \
	apc.enabled=1 \
	apc.enable_cli=1

management/editconf.py /etc/php/$PHP_VER/cli/php.ini -c ';' \
	apc.enable_cli=1

# The PHP interpreter used to run Nextcloud's command line tools. Upgrades through
# old Nextcloud versions may temporarily switch it to an older PHP version, see below.
NEXTCLOUD_PHP=php$PHP_VER

function occ {
	# Runs a Nextcloud command (e.g. occ upgrade) with $NEXTCLOUD_PHP.
	sudo -u www-data $NEXTCLOUD_PHP -d apc.enable_cli=1 /usr/local/lib/owncloud/occ "$@"
}

function install_legacy_php {
	# Older Nextcloud versions refuse to run under PHP $PHP_VER. Install the
	# (command line) PHP version we use to run their upgrade steps.
	echo "Installing PHP $LEGACY_PHP_VER to upgrade from an older Nextcloud version..."
	apt_install php${LEGACY_PHP_VER}-cli php${LEGACY_PHP_VER}-sqlite3 php${LEGACY_PHP_VER}-gd \
		php${LEGACY_PHP_VER}-curl php${LEGACY_PHP_VER}-xml php${LEGACY_PHP_VER}-mbstring \
		php${LEGACY_PHP_VER}-zip php${LEGACY_PHP_VER}-apcu php${LEGACY_PHP_VER}-intl \
		php${LEGACY_PHP_VER}-gmp php${LEGACY_PHP_VER}-bcmath
	hide_output update-alternatives --set php /usr/bin/php${PHP_VER}
	NEXTCLOUD_PHP=php$LEGACY_PHP_VER
}

function remove_legacy_php {
	# Done with the upgrade steps that needed an older PHP version.
	NEXTCLOUD_PHP=php$PHP_VER
	apt_get_quiet purge $(dpkg-query -W -f='${Package}\n' "php${LEGACY_PHP_VER}*")
	hide_output update-alternatives --set php /usr/bin/php${PHP_VER}
}

InstallNextcloud() {

	version=$1
	hash=$2
	version_contacts=$3
	hash_contacts=$4
	version_calendar=$5
	hash_calendar=$6
	version_user_external=${7:-}
	hash_user_external=${8:-}

	echo
	echo "Upgrading to Nextcloud version $version"
	echo

	# Download and verify
	wget_verify https://download.nextcloud.com/server/releases/nextcloud-$version.zip $hash /tmp/nextcloud.zip

	# Remove the current owncloud/Nextcloud
	rm -rf /usr/local/lib/owncloud

	# Extract ownCloud/Nextcloud
	unzip -q /tmp/nextcloud.zip -d /usr/local/lib
	mv /usr/local/lib/nextcloud /usr/local/lib/owncloud
	rm -f /tmp/nextcloud.zip

	# The two apps we actually want are not in Nextcloud core. Download the releases from
	# their github repositories.
	mkdir -p /usr/local/lib/owncloud/apps

	wget_verify https://github.com/nextcloud-releases/contacts/releases/download/v$version_contacts/contacts-v$version_contacts.tar.gz $hash_contacts /tmp/contacts.tgz
	tar xf /tmp/contacts.tgz -C /usr/local/lib/owncloud/apps/
	rm /tmp/contacts.tgz

	wget_verify https://github.com/nextcloud-releases/calendar/releases/download/v$version_calendar/calendar-v$version_calendar.tar.gz $hash_calendar /tmp/calendar.tgz
	tar xf /tmp/calendar.tgz -C /usr/local/lib/owncloud/apps/
	rm /tmp/calendar.tgz

	# Starting with Nextcloud 15, the app user_external is no longer included in Nextcloud core,
	# we will install from their github repository. (Some Nextcloud versions have no compatible
	# release, in which case Nextcloud disables the app during that upgrade step.)
	if [ -n "$version_user_external" ]; then
		wget_verify https://github.com/nextcloud-releases/user_external/releases/download/v$version_user_external/user_external-v$version_user_external.tar.gz $hash_user_external /tmp/user_external.tgz
		tar -xf /tmp/user_external.tgz -C /usr/local/lib/owncloud/apps/
		rm /tmp/user_external.tgz
	fi

	# Fix weird permissions.
	chmod 750 /usr/local/lib/owncloud/{apps,config}

	# Create a symlink to the config.php in STORAGE_ROOT (for upgrades we're restoring the symlink we previously
	# put in, and in new installs we're creating a symlink and will create the actual config later).
	ln -sf $STORAGE_ROOT/owncloud/config.php /usr/local/lib/owncloud/config/config.php

	# Make sure permissions are correct or the upgrade step won't run.
	# $STORAGE_ROOT/owncloud may not yet exist, so use -f to suppress
	# that error.
	chown -f -R www-data:www-data $STORAGE_ROOT/owncloud /usr/local/lib/owncloud || /bin/true

	# If this isn't a new installation, immediately run the upgrade script.
	if [ -e $STORAGE_ROOT/owncloud/owncloud.db ]; then
		# ownCloud 8.1.1 broke upgrades. It may fail on the first attempt, but
		# that can be OK.
		if ! occ upgrade; then
			echo "Trying Nextcloud upgrade again to work around Nextcloud upgrade bug..."
			occ upgrade
			occ maintenance:mode --off
			echo "...which seemed to work."
		fi

		# Add missing indices and primary keys. Nextcloud didn't include this in the normal
		# upgrade because it might take some time.
		occ db:add-missing-indices
		occ db:add-missing-primary-keys

		# Run conversion to BigInt identifiers, this process may take some time on large tables.
		occ db:convert-filecache-bigint --no-interaction
	fi
}

# Current Nextcloud Version, #1623
# Checking /usr/local/lib/owncloud/version.php shows version of the Nextcloud application, not the DB
# $STORAGE_ROOT/owncloud is kept together even during a backup.  It is better to rely on config.php than
# version.php since the restore procedure can leave the system in a state where you have a newer Nextcloud
# application version than the database.
# If config.php exists, get version number, otherwise CURRENT_NEXTCLOUD_VER is empty.
#
# Config unlocking, power-mailinabox#86
# If a configuration file already exists, remove the "readonly" tag before starting the upgrade. This is
# necessary (otherwise upgrades will fail).
#
# The lock will be re-applied further down the line when it's safe to do so.
CONFIG_TEMP=$(/bin/mktemp)
if [ -f "$STORAGE_ROOT/owncloud/config.php" ]; then
	CURRENT_NEXTCLOUD_VER=$(php$PHP_VER -r "include(\"$STORAGE_ROOT/owncloud/config.php\"); echo(\$CONFIG['version']);")
	# Unlock configuration directory for upgrades
	php$PHP_VER <<EOF > $CONFIG_TEMP && mv $CONFIG_TEMP $STORAGE_ROOT/owncloud/config.php;
<?php
include("$STORAGE_ROOT/owncloud/config.php");

\$CONFIG['config_is_read_only'] = false;

echo "<?php\n\\\$CONFIG = ";
var_export(\$CONFIG);
echo ";";
?>
EOF
	chown www-data:www-data $STORAGE_ROOT/owncloud/config.php
else
	CURRENT_NEXTCLOUD_VER=""
fi

# If the Nextcloud directory is missing (never been installed before, or the nextcloud version to be installed is different
# from the version currently installed, do the install/upgrade
if [ ! -d /usr/local/lib/owncloud/ ] || [[ ! ${CURRENT_NEXTCLOUD_VER} =~ ^$nextcloud_ver ]]; then

	# Don't stop php-fpm during the upgrade: other sites on the box may use it too.
	# The upgrade puts Nextcloud in maintenance mode, and php-fpm is restarted at
	# the end of this script.

	# Backup the existing ownCloud/Nextcloud.
	# Create a backup directory to store the current installation and database to
	BACKUP_DIRECTORY=$STORAGE_ROOT/owncloud-backup/$(date +"%Y-%m-%d-%T")
	mkdir -p "$BACKUP_DIRECTORY"
	if [ -d /usr/local/lib/owncloud/ ]; then
		echo "Upgrading Nextcloud --- backing up existing installation, configuration, and database to directory to $BACKUP_DIRECTORY..."
		cp -r /usr/local/lib/owncloud "$BACKUP_DIRECTORY/owncloud-install"
	fi
	if [ -e $STORAGE_ROOT/owncloud/owncloud.db ]; then
		cp $STORAGE_ROOT/owncloud/owncloud.db $BACKUP_DIRECTORY
	fi
	if [ -e $STORAGE_ROOT/owncloud/config.php ]; then
		cp $STORAGE_ROOT/owncloud/config.php $BACKUP_DIRECTORY
	fi

	# If ownCloud or Nextcloud was previously installed....
	if [ ! -z ${CURRENT_NEXTCLOUD_VER} ]; then
		# Nextcloud versions older than 23 can't run on any PHP version we can install, so we can't
		# migrate their database.
		if [[ ${CURRENT_NEXTCLOUD_VER} =~ ^([89]|1[0-9]|2[0-2])\. ]]; then
			echo "Upgrades from Nextcloud $CURRENT_NEXTCLOUD_VER are not supported by this version of Power Mail-in-a-Box,"
			echo "since that Nextcloud version is too old to run on PHP $LEGACY_PHP_VER or newer. Upgrade to Power Mail-in-a-Box"
			echo "v60.5 first (on an operating system it supports). Setup will continue, but skip the Nextcloud migration."
			return 0
		fi

		# Nextcloud versions up to 30 don't support PHP $PHP_VER, so their upgrade steps are run
		# with an older PHP version.
		if [[ ${CURRENT_NEXTCLOUD_VER} =~ ^(2[3-9])\. ]]; then
			install_legacy_php
		fi

		if [[ ${CURRENT_NEXTCLOUD_VER} =~ ^23\. ]]; then
			InstallNextcloud 24.0.12 7aa5d61632c1ccf4ca3ff00fb6b295d318c05599 4.2.5 f318636decb3b7276c1e63a06de61dcb10f04bbf 3.5.9 cb3b4df6c9aa99bfc055ab56ba4b7fdcd8e4629d 3.1.0 22cabc88b6fc9c26dad3b46be1a652979c9fcf15
			CURRENT_NEXTCLOUD_VER="24.0.12"
		fi
		if [[ ${CURRENT_NEXTCLOUD_VER} =~ ^24\. ]]; then
			InstallNextcloud 25.0.13 eaba90f0fedefade9b05ef40844df98d361259b7 5.5.4 c4e3f2183a0088b829f8aa1b3af1f87c9a4c46a2 4.6.8 b01187e58a18a35774ed6fa97c1d336454208ddd 3.4.0 7f9d8f4dd6adb85a0e3d7622d85eeb7bfe53f3b4
			CURRENT_NEXTCLOUD_VER="25.0.13"
		fi
		if [[ ${CURRENT_NEXTCLOUD_VER} =~ ^25\. ]]; then
			InstallNextcloud 26.0.13 d5c10b650e5396d5045131c6d22c02a90572527c 5.5.4 c4e3f2183a0088b829f8aa1b3af1f87c9a4c46a2 4.7.20 12d876904e227156e39ca4335b18481b42a6d00f 3.4.0 7f9d8f4dd6adb85a0e3d7622d85eeb7bfe53f3b4
			CURRENT_NEXTCLOUD_VER="26.0.13"
		fi
		if [[ ${CURRENT_NEXTCLOUD_VER} =~ ^26\. ]]; then
			InstallNextcloud 27.1.11 9f30c01a021c2e5a9e7baff119955afb3c552ebc 5.5.4 c4e3f2183a0088b829f8aa1b3af1f87c9a4c46a2 4.7.20 12d876904e227156e39ca4335b18481b42a6d00f 3.4.0 7f9d8f4dd6adb85a0e3d7622d85eeb7bfe53f3b4
			CURRENT_NEXTCLOUD_VER="27.1.11"
		fi
		if [[ ${CURRENT_NEXTCLOUD_VER} =~ ^27\. ]]; then
			InstallNextcloud 28.0.14 8a9edcfd26d318eb7d1cfa44d69796f2d1098a80 5.5.4 c4e3f2183a0088b829f8aa1b3af1f87c9a4c46a2 4.7.20 12d876904e227156e39ca4335b18481b42a6d00f 3.4.0 7f9d8f4dd6adb85a0e3d7622d85eeb7bfe53f3b4
			CURRENT_NEXTCLOUD_VER="28.0.14"
		fi
		if [[ ${CURRENT_NEXTCLOUD_VER} =~ ^28\. ]]; then
			InstallNextcloud 29.0.16 ceb3014aaddc70d3074d2c69bc6afc76eb1aeff0 6.0.7 babb779107b029c30ad20b81da33b4f95e1136ff 4.7.20 12d876904e227156e39ca4335b18481b42a6d00f 3.4.0 7f9d8f4dd6adb85a0e3d7622d85eeb7bfe53f3b4
			CURRENT_NEXTCLOUD_VER="29.0.16"
		fi
		if [[ ${CURRENT_NEXTCLOUD_VER} =~ ^29\. ]]; then
			# There's no user_external release supporting Nextcloud 30: Nextcloud will disable
			# it during this step, and it is enabled again further below.
			InstallNextcloud 30.0.17 0494197f1984ce8a2f83084c0759a24d48474017 7.3.19 bd680b3b96f09d013ff3a12eccb352b83f3bcd51 5.5.24 a7722f0511a1165e6a51c1944288ed4518723791
			CURRENT_NEXTCLOUD_VER="30.0.17"
		fi

		# From here on, Nextcloud supports PHP $PHP_VER.
		if [ "$NEXTCLOUD_PHP" != "php$PHP_VER" ]; then
			remove_legacy_php
		fi

		if [[ ${CURRENT_NEXTCLOUD_VER} =~ ^30\. ]]; then
			InstallNextcloud 31.0.14 a891fede2cd4cb3347a406da3fb4f99cd62c89ce 7.3.19 bd680b3b96f09d013ff3a12eccb352b83f3bcd51 5.5.24 a7722f0511a1165e6a51c1944288ed4518723791 4.0.0 214497dd8691f279ba3740797c565310f0793054
			CURRENT_NEXTCLOUD_VER="31.0.14"
		fi
		if [[ ${CURRENT_NEXTCLOUD_VER} =~ ^31\. ]]; then
			InstallNextcloud 32.0.15 f13d45819bf2ec3fe83c9c3078324144eff45568 8.3.20 10d1617df0cde3edcc913e94e9a1ba8ad1084827 6.6.2 f396ede3d50b15e4975319de7cae58b035f30b4c 4.0.0 214497dd8691f279ba3740797c565310f0793054
			CURRENT_NEXTCLOUD_VER="32.0.15"
		fi
		if [[ ${CURRENT_NEXTCLOUD_VER} =~ ^32\. ]]; then
			InstallNextcloud 33.0.10 debd92e9249192368108975433035e7b647563d9 8.9.1 ec22abf78e5caf796b326c4838b1162d29970a9e 6.6.2 f396ede3d50b15e4975319de7cae58b035f30b4c 4.0.0 214497dd8691f279ba3740797c565310f0793054
			CURRENT_NEXTCLOUD_VER="33.0.10"
		fi
	fi

	InstallNextcloud $nextcloud_ver $nextcloud_hash $contacts_ver $contacts_hash $calendar_ver $calendar_hash $user_external_ver $user_external_hash
fi

# InstallNextcloud installs the apps along with Nextcloud itself. On boxes that already run
# this version of Nextcloud, update the apps whose versions changed.
function UpdateNextcloudApp {
	app=$1
	version=$2
	hash=$3

	installed=$(sed -n 's|^\s*<version>\(.*\)</version>|\1|p' /usr/local/lib/owncloud/apps/$app/appinfo/info.xml 2> /dev/null | head -n 1 || /bin/true)
	if [ "$installed" == "$version" ]; then
		return 1
	fi

	echo "Updating the Nextcloud app $app to version $version..."
	wget_verify https://github.com/nextcloud-releases/$app/releases/download/v$version/$app-v$version.tar.gz $hash /tmp/$app.tgz
	rm -rf /usr/local/lib/owncloud/apps/$app
	tar -xf /tmp/$app.tgz -C /usr/local/lib/owncloud/apps/
	rm /tmp/$app.tgz
	chown -R www-data:www-data /usr/local/lib/owncloud/apps/$app
}
if [ -e $STORAGE_ROOT/owncloud/owncloud.db ]; then
	apps_updated=0
	UpdateNextcloudApp contacts $contacts_ver $contacts_hash && apps_updated=1
	UpdateNextcloudApp calendar $calendar_ver $calendar_hash && apps_updated=1
	UpdateNextcloudApp user_external $user_external_ver $user_external_hash && apps_updated=1
	if [ $apps_updated == 1 ]; then
		# Run the apps' database migrations.
		occ upgrade
	fi
fi

# ### Configuring Nextcloud

# Setup Nextcloud if the Nextcloud database does not yet exist. Running setup when
# the database does exist wipes the database and user data.
if [ ! -f $STORAGE_ROOT/owncloud/owncloud.db ]; then
	# Create user data directory
	mkdir -p $STORAGE_ROOT/owncloud

	# Create an initial configuration file.
	instanceid=oc$(echo $PRIMARY_HOSTNAME | sha1sum | fold -w 10 | head -n 1)
	cat > $STORAGE_ROOT/owncloud/config.php <<EOF;
<?php
\$CONFIG = array (
  'datadirectory' => '$STORAGE_ROOT/owncloud',

  'instanceid' => '$instanceid',

  'forcessl' => true, # if unset/false, Nextcloud sends a HSTS=0 header, which conflicts with nginx config

  'overwritewebroot' => '/cloud',
  'overwrite.cli.url' => 'https://${PRIMARY_HOSTNAME}/cloud',
  'user_backends' => array(
    array(
      'class' => '\OCA\UserExternal\IMAP',
      'arguments' => array(
        '127.0.0.1', 143, null, null, false, false
       ),
    ),
  ),
  'memcache.local' => '\OC\Memcache\APCu',
  'mail_smtpmode' => 'sendmail',
  'mail_smtpsecure' => '',
  'mail_smtpauthtype' => 'LOGIN',
  'mail_smtpauth' => false,
  'mail_smtphost' => '',
  'mail_smtpport' => '',
  'mail_smtpname' => '',
  'mail_smtppassword' => '',
  'mail_from_address' => 'owncloud',
);
?>
EOF

	# Create an auto-configuration file to fill in database settings
	# when the install script is run. Make an administrator account
	# here or else the install can't finish.
	adminpassword=$(dd if=/dev/urandom bs=1 count=40 2>/dev/null | sha1sum | fold -w 30 | head -n 1)
	cat > /usr/local/lib/owncloud/config/autoconfig.php <<EOF;
<?php
\$AUTOCONFIG = array (
  # storage/database
  'directory' => '$STORAGE_ROOT/owncloud',
  'dbtype' => 'sqlite3',
  'dbname' => 'owncloud',

  # create an administrator account with a random password so that
  # the user does not have to enter anything on first load of Nextcloud
  'adminlogin'    => 'root',
  'adminpass'     => '$adminpassword',
);
?>
EOF

	# Set permissions
	chown -R www-data:www-data $STORAGE_ROOT/owncloud /usr/local/lib/owncloud

	# Execute Nextcloud's setup step, which creates the Nextcloud sqlite database.
	# It also wipes it if it exists. And it updates config.php with database
	# settings and deletes the autoconfig.php file.
	(cd /usr/local/lib/owncloud; sudo -u www-data php$PHP_VER -d apc.enable_cli=1 /usr/local/lib/owncloud/index.php;)
fi

# Update config.php.
# * trusted_domains is reset to localhost by autoconfig starting with ownCloud 8.1.1,
#   so set it here. It also can change if the box's PRIMARY_HOSTNAME changes, so
#   this will make sure it has the right value.
# * Some settings weren't included in previous versions of Mail-in-a-Box.
# * We need to set the timezone to the system timezone to allow fail2ban to ban
#   users within the proper timeframe
# * We need to set the logdateformat to something that will work correctly with fail2ban
# * mail_domain' needs to be set every time we run the setup. Making sure we are setting
#   the correct domain name if the domain is being change from the previous setup.
# * The app store is disabled: we install and update the apps we support ourselves, and
#   Nextcloud updating apps on its own has broken boxes in the past.
# Use PHP to read the settings file, modify it, and write out the new settings array.
TIMEZONE=$(cat /etc/timezone)
php$PHP_VER <<EOF > $CONFIG_TEMP && mv $CONFIG_TEMP $STORAGE_ROOT/owncloud/config.php;
<?php
include("$STORAGE_ROOT/owncloud/config.php");

\$CONFIG['config_is_read_only'] = false;
\$CONFIG['appstoreenabled'] = false;

\$CONFIG['trusted_domains'] = array('$PRIMARY_HOSTNAME');

\$CONFIG['memcache.local'] = '\OC\Memcache\APCu';
\$CONFIG['overwrite.cli.url'] = 'https://${PRIMARY_HOSTNAME}/cloud';
\$CONFIG['mail_from_address'] = 'administrator'; # just the local part, matches our master administrator address
\$CONFIG['mail_smtpmode'] = 'sendmail';
\$CONFIG['mail_smtpauth'] = true; # if smtpmode is smtp
\$CONFIG['mail_smtphost'] = '127.0.0.1'; # if smtpmode is smtp
\$CONFIG['mail_smtpport'] = '587'; # if smtpmode is smtp
\$CONFIG['mail_smtpsecure'] = ''; # if smtpmode is smtp, must be empty string
\$CONFIG['mail_smtpname'] = ''; # if smtpmode is smtp, set this to a mail user
\$CONFIG['mail_smtppassword'] = ''; # if smtpmode is smtp, set this to the user's password

\$CONFIG['logtimezone'] = '$TIMEZONE';
\$CONFIG['logdateformat'] = 'Y-m-d H:i:s';

\$CONFIG['mail_domain'] = '$PRIMARY_HOSTNAME';

\$CONFIG['user_backends'] = array(
  array(
    'class' => '\OCA\UserExternal\IMAP',
    'arguments' => array(
      '127.0.0.1', 143, null, null, false, false
    ),
  ),
);

echo "<?php\n\\\$CONFIG = ";
var_export(\$CONFIG);
echo ";";
?>
EOF
chown www-data:www-data $STORAGE_ROOT/owncloud/config.php

# Enable/disable apps. Note that this must be done after the Nextcloud setup.
# The firstrunwizard gave Josh all sorts of problems, so disabling that.
# user_external is what allows Nextcloud to use IMAP for login. The contacts
# and calendar apps are the extensions we really care about here.
hide_output occ app:disable firstrunwizard
hide_output occ app:enable user_external
hide_output occ app:enable contacts
hide_output occ app:enable calendar

# When upgrading, run the upgrade script again now that apps are enabled. It seems like
# the first upgrade at the top won't work because apps may be disabled during upgrade?
occ upgrade

# Disable default apps that we don't support
occ app:disable photos dashboard activity \
	| (grep -v "No such app enabled" || /bin/true) || /bin/true

# Set PHP FPM values to support large file uploads
# (semicolon is the comment character in this file, hashes produce deprecation warnings)
management/editconf.py /etc/php/$PHP_VER/fpm/php.ini -c ';' \
	upload_max_filesize=16G \
	post_max_size=16G \
	output_buffering=16384 \
	memory_limit=512M \
	max_execution_time=600 \
	short_open_tag=On

# Set Nextcloud recommended opcache settings
management/editconf.py /etc/php/$PHP_VER/cli/conf.d/10-opcache.ini -c ';' \
	opcache.enable=1 \
	opcache.enable_cli=1 \
	opcache.interned_strings_buffer=8 \
	opcache.max_accelerated_files=10000 \
	opcache.memory_consumption=128 \
	opcache.save_comments=1 \
	opcache.revalidate_freq=1

# Migrate users_external data from <0.6.0 to version 3.0.0 (see https://github.com/nextcloud/user_external).
# This version was probably in use in Mail-in-a-Box v0.41 (February 26, 2019) and earlier.
# We moved to v0.6.3 in 193763f8. Ignore errors - maybe there are duplicated users with the
# correct backend already.
sqlite3 $STORAGE_ROOT/owncloud/owncloud.db "UPDATE oc_users_external SET backend='127.0.0.1';" || /bin/true

# Set up a general cron job for Nextcloud.
# Also add another job for Calendar updates, per advice in the Nextcloud docs
# https://docs.nextcloud.com/server/latest/admin_manual/groupware/calendar.html#background-jobs
cat > /etc/cron.d/mailinabox-nextcloud << EOF;
#!/bin/bash
# Mail-in-a-Box
*/5 * * * *	www-data	php$PHP_VER -d apc.enable_cli=1 -f /usr/local/lib/owncloud/cron.php
*/5 * * * *	www-data	php$PHP_VER -d apc.enable_cli=1 -f /usr/local/lib/owncloud/occ dav:send-event-reminders
EOF
chmod +x /etc/cron.d/mailinabox-nextcloud

# We also need to change the sending mode from background-job to occ.
# Or else the reminders will just be sent as soon as possible when the background jobs run.
hide_output occ config:app:set dav sendEventRemindersMode --value occ

# Now set the config to read-only (power-mailinabox#86).
# Do this only at the very bottom when no further occ commands are needed.
sed -i'' "s/'config_is_read_only'\s*=>\s*false/'config_is_read_only' => true/" $STORAGE_ROOT/owncloud/config.php

# Rotate the nextcloud.log file
cat > /etc/logrotate.d/nextcloud <<EOF
# Nextcloud logs
$STORAGE_ROOT/owncloud/nextcloud.log {
		size 10M
		create 640 www-data www-data
		rotate 30
		copytruncate
		missingok
		compress
}
EOF

# There's nothing much of interest that a user could do as an admin for Nextcloud,
# and there's a lot they could mess up, so we don't make any users admins of Nextcloud.
# But if we wanted to, we would do this:
# ```
# for user in $(management/cli.py user admins); do
#	 sqlite3 $STORAGE_ROOT/owncloud/owncloud.db "INSERT OR IGNORE INTO oc_group_user VALUES ('admin', '$user')"
# done
# ```

# Enable PHP modules and restart PHP.
restart_service php$PHP_VER-fpm
