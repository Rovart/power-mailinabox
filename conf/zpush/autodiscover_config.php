<?php
/***********************************************
* File      :   config.php
* Project   :   Z-Push
* Descr     :   Autodiscover configuration file
************************************************/

define('TIMEZONE', '');

// Defines the base path on the server
define('BASE_PATH', dirname($_SERVER['SCRIPT_FILENAME']). '/');

define('ZPUSH_HOST', 'PRIMARY_HOSTNAME');

define('USE_FULLEMAIL_FOR_LOGIN', true);

define('LOGFILEDIR', '/var/log/z-push/');
define('LOGFILE', LOGFILEDIR . 'autodiscover.log');
define('LOGERRORFILE', LOGFILEDIR . 'autodiscover-error.log');
define('LOGLEVEL', LOGLEVEL_INFO);
define('LOGUSERLEVEL', LOGLEVEL);
$specialLogUsers = array();

// the backend data provider
define('BACKEND_PROVIDER', 'BackendCombined');

// Whether the CalDAV and CardDAV backends verify the servers' TLS certificates: yes, except
// for servers on the box itself (like ours). Z-Push 2.7.7 only sets this default for
// ActiveSync, not for autodiscover, which then fails with "Undefined constant DAV_SSL_VERIFY".
define('DAV_SSL_VERIFY', DAV_SSL_VERIFY_AUTO);
?>
