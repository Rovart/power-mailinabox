# Modernization plan (Rovart fork)

Power Mail-in-a-Box stopped at v60.5 (Nov 2022). This fork keeps every feature that
v60.5 ships — including Nextcloud (contacts/calendar) and Z-Push (Exchange ActiveSync) —
and brings the stack back onto supported versions, starting with PHP.

Constraint: no feature that is active on an existing v60.5 box may be lost, and an
existing box must upgrade in place by re-running `setup/start.sh`.

## Targets

| Component  | v60.5        | Target          | Notes |
|------------|--------------|-----------------|-------|
| PHP        | distro 8.1   | 8.4 (ppa:ondrej/php, pinned to `php8.4*`, `php-common`, `libgd3`) | 8.1 is upstream EOL; Ubuntu 22.04 standard support ends Apr 2027 |
| Roundcube  | 1.6.0        | 1.6.20          | same branch: config and plugins stay compatible; 1.6.0 has exploited XSS fixes missing |
| Z-Push     | 2.6.2        | 2.7.6           | verify PHP 8.4 (IMAP backend, CardDAV/CalDAV backends, autodiscover) |
| Nextcloud  | 24.0.7       | 33.x or later   | one major version per upgrade step (24 → 25 → … → target); contacts, calendar and user_external app versions per step |

## Work items

1. **PHP version as one variable.** `setup/web.sh` and `setup/nextcloud.sh` install the
   unversioned `php`/`php-fpm` metapackages and write the `php-default` pool. Introduce
   `PHP_VER=8.4`, install `php${PHP_VER}-*` from the pinned PPA, and point the existing
   `php-default` pool and the `php.default` CLI alternative at `${PHP_VER}`. On upgrade,
   stop the old version's pool so the `php-default.sock` path is free.
   Lessons from migrating a live v60.5 box by hand (Oct 2026):
   - the PPA's `php-common` breaks Ubuntu's `php-apcu`, `php-gnupg`, `php-imagick`,
     `php-imap`, `php-pspell` metapackages: install the versioned extensions instead;
   - `php8.4-gd` needs the PPA's `libgd3`;
   - PHP 8.4 moved `imap` and `pspell` out of core: Z-Push's IMAP backend needs the
     PPA's `php8.4-imap` (PECL build).
2. **Roundcube 1.6.0 → 1.6.20**, plus its plugins (persistent_login, carddav).
3. **Z-Push 2.6.2 → 2.7.6**, validated on PHP 8.4.
4. **Nextcloud stepwise upgrade** from 24.0.7 to the target (port the stepwise logic of
   upstream Mail-in-a-Box `setup/nextcloud.sh`, extended past 27), app versions per step.
5. **Port relevant fixes** from upstream Mail-in-a-Box v61–v77 (security first).
6. **CI**: GitHub Actions job that runs a fresh install and an upgrade from v60.5 on
   `ubuntu-22.04`, then `tests/`.
7. Later: Ubuntu 24.04 support.

## Rollout

Staging box (fresh v60.5 install → upgrade) before any production box; snapshot the
production VM before running setup there.
