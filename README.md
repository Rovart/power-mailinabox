# Enhanced Mail-in-a-Box

A complete, self-hosted mail server that you can deploy on a fresh Debian or Ubuntu machine in a few minutes, and keep up to date with a single command.

Enhanced Mail-in-a-Box is a maintained fork of [Power Mail-in-a-Box](https://github.com/ddavness/power-mailinabox) by David Duque, itself based on [Mail-in-a-Box](https://mailinabox.email/). Up to v61.18 it was also called Power Mail-in-a-Box.

**Current version: v61.19** · [Install](#installation) · [Upgrade](#upgrading) · [Changelog](CHANGELOG.md) · [Releases](https://github.com/Rovart/enhanced-mailinabox/releases)

## What it adds

Mail-in-a-Box gives you a mail server with sane defaults that work out of the box. Power Mail-in-a-Box added the features advanced users ask for. Enhanced Mail-in-a-Box keeps it all up to date and protects the box against the attacks every mail server gets.

**Up to date**
- Debian 12 and 13, Ubuntu 22.04 and 24.04, all with **PHP 8.4**. PHP security releases are installed automatically every day, like the operating system's own.
- Current versions of the bundled applications: Nextcloud 34, Roundcube 1.7 and Z-Push 2.7, with existing installations upgraded automatically.
- The fixes made to Mail-in-a-Box since the original project stopped (up to v77, including protection against SMTP smuggling), and security fixes released as soon as they're out.
- Works behind a reverse proxy or CDN (e.g. Cloudflare's proxy) and with DNS hosted elsewhere, without false alarms in the status checks.

**Protection against attacks**
- **IP blocklists**, on by default: public lists of attackers (blocklist.de, Spamhaus DROP, and more you can add with one click) and your own list, each for the login ports only or for every port. Your users' own networks are never blocked.
- **Country filter**: allow only the countries your users connect from, or block the ones you choose. Your local network, Tailscale and other VPNs always get through.
- **An email when an account logs in from a new place** (another internet provider or country), so a stolen password shows up. A table of every login, with the approximate city, country and provider.
- **A security dashboard**: failed logins, bans and blocked connections over 24 hours, 7 or 30 days, a world map of where attacks come from, the most active attackers with a Block button, and the blocked addresses downloadable for any CDN or WAF.
- Slower fail2ban rules for password guessing spread over time, a limit on login attempts per address, and a firewall that keeps your own SSH rules.

**Control panel**
- A new design: a sidebar with every section in view, light and dark themes, usable on phones and with the keyboard.
- **Mail filtering**: let the mail servers you trust skip greylisting, and refuse mail from senders, domains or servers you block.
- **Optional services**: turn Nextcloud, Exchange ActiveSync, Roundcube or Munin off and back on. Their data is kept and backed up.
- **Local-only access**: make the control panel, webmail, Nextcloud or Exchange ActiveSync reachable only from the box itself (e.g. through an SSH tunnel or a VPN) and from trusted networks.
- Backups on demand.

**From Power Mail-in-a-Box**
- **SMTP relays:** send outgoing mail through an external relay.
- **Mailbox quotas,** with a default for new users (thanks to [@jrsupplee](https://github.com/jrsupplee/mailinabox)).
- **Custom TTLs** for custom DNS records.
- **OpenPGP keys:** keep a keyring and publish keys for your domains through Web Key Directory (WKD).
- **Per-domain web server configuration:** `/home/user-data/www/<domain>.conf` is included in that domain's nginx configuration, so you can add PHP, proxies or anything else nginx supports.
- **Debian support,** besides Ubuntu.

## Latest release: v61.19

- **Country filter** (Mail > Filtering > Login protection), off by default.
- **A new name**: Enhanced Mail-in-a-Box, at [Rovart/enhanced-mailinabox](https://github.com/Rovart/enhanced-mailinabox). The old address redirects here, and boxes upgrade as usual.
- **Security**: the control panel's API answers in plain text, and refuses changes that another website makes your browser send.
- Rebooting from the control panel no longer ends with an error; Nextcloud 34.0.6; on Ubuntu, PHP updates from packages.sury.org are installed automatically too.

Earlier versions are described in the [changelog](CHANGELOG.md) and the [releases](https://github.com/Rovart/enhanced-mailinabox/releases).

## About this fork

[Power Mail-in-a-Box](https://github.com/ddavness/power-mailinabox) was created by David Duque, and its repository has not been updated since v60.5 (November 2022). Enhanced Mail-in-a-Box keeps the project maintained. It has its own name so that it isn't mistaken for the original project: it is not affiliated with or endorsed by its author. Everything here is released under the same public domain dedication as the original (CC0), so any of it can be taken back into the original project.

Boxes installed from the original repository can switch to Enhanced Mail-in-a-Box with the usual upgrade command (see [Upgrading](#upgrading)). This repository used to be `Rovart/power-mailinabox`: the old address redirects here, and boxes installed from it upgrade as usual.

## What's in the box

**Mail**
- SMTP with Postfix and IMAP, POP3 and ManageSieve with Dovecot, all over TLS.
- Spam filtering with SpamAssassin, greylisting and the Spamhaus block lists.
- DKIM signing, SPF and DMARC records, MTA-STS and DANE.
- Automatic configuration for mail clients (Thunderbird autoconfig, Exchange autodiscover, iOS and macOS profiles).

**Webmail, contacts, calendars and files**
- Roundcube webmail.
- Nextcloud for contacts, calendars and files, with CalDAV and CardDAV.
- Exchange ActiveSync with Z-Push, for phones and Outlook.

**DNS, certificates and websites**
- An authoritative DNS server (nsd) with DNSSEC, or instructions to use an external DNS provider.
- Free TLS certificates from Let's Encrypt, provisioned and renewed automatically.
- Static websites for every domain.

**Administration**
- A web control panel with two-factor authentication, to manage users, aliases, DNS, certificates, websites and backups.
- System status checks, with a daily email when something needs attention, and Munin monitoring.
- Encrypted backups (duplicity) to the box itself, an rsync server, Amazon S3 or another S3-compatible service, or Backblaze B2.
- A firewall (ufw) and protection against brute-force logins (fail2ban).

### Who it's for

Individuals and small and medium-sized organizations who want to run their own mail. It's not meant for mission-critical deployments that need commercial support.

## Requirements

**Machine**
- A fresh installation of Debian 12 (bookworm), Debian 13 (trixie), Ubuntu 22.04 LTS or Ubuntu 24.04 LTS, on x86_64.
- 1 CPU core, 1 GB of RAM (512 MB is the minimum) and 10 GB of disk. Needs grow with the number of users and the amount of mail.
- `/tmp` mounted with exec permissions.

PHP 8.4 (8.4.27 or newer) is installed from [packages.sury.org](https://deb.sury.org) on Debian and from the [ondrej/php](https://launchpad.net/~ondrej/+archive/ubuntu/php) PPA on Ubuntu, so every operating system runs the same version.

**Network**
- A static public IPv4 address, with reverse DNS pointing to the box's hostname.
- Outbound port 25 open. Many cloud providers block it by default and unblock it on request.

Residential connections rarely meet these requirements. The box can still be installed, but mail delivery will need more work.

**Firewall**

The box configures its own firewall. If it is behind an external firewall or NAT, open these inbound ports:

| Port | Service |
| --- | --- |
| 22/tcp | SSH (or the port your SSH server uses) |
| 25/tcp | SMTP (incoming mail) |
| 53/tcp, 53/udp | DNS |
| 80/tcp, 443/tcp | HTTP and HTTPS |
| 465/tcp, 587/tcp | SMTP submission (sending mail) |
| 993/tcp | IMAP |
| 995/tcp | POP3 |
| 4190/tcp | ManageSieve (mail filters) |

## Installation

1. Make sure `curl` is installed and the primary locale is `en_US.UTF-8`:

   ```
   sudo apt install curl locales
   sudo dpkg-reconfigure locales
   ```

2. Run the setup and follow the instructions on the screen:

   ```
   curl -L https://raw.githubusercontent.com/Rovart/enhanced-mailinabox/v61.19/setup/bootstrap.sh | sudo bash
   ```

3. Open the control panel at `https://<your box's hostname>/admin` and follow the status checks to finish the DNS configuration.

The box manages its firewall with `ufw`. Your own rules for SSH are left as they are; the setup only opens SSH (rate limited) on a new box. Some cloud providers (Oracle Cloud, for example) add firewall rules of their own that have to be adjusted; see [these instructions](https://github.com/ddavness/power-mailinabox/discussions/21).

## Upgrading

Run the same command as for a new installation:

```
curl -L https://raw.githubusercontent.com/Rovart/enhanced-mailinabox/v61.19/setup/bootstrap.sh | sudo bash
```

It downloads the new version and runs the setup again, keeping your data and settings. To be told when a new version is out, turn on *Check for new versions* in the control panel's status checks (it is off by default). To run the setup again without upgrading, use `sudo mailinabox`.

**From the original Power Mail-in-a-Box.** The same command works: on a box installed from [ddavness/power-mailinabox](https://github.com/ddavness/power-mailinabox), it switches the box's copy of the code to Enhanced Mail-in-a-Box.

**From Mail-in-a-Box.** There is no automatic migration. Install Enhanced Mail-in-a-Box on a new machine and move your mail and settings over.

### Operating system upgrades

To move a box to a newer operating system (e.g. from Debian 12 to Debian 13), upgrade the operating system following its own instructions, **reboot**, and run the setup again. Until then, some services may not work, since their configuration is for the older versions.

These operating systems are no longer supported. Boxes running them can install the last version that supported them, and then upgrade the operating system:

| Operating system | Last version |
| --- | --- |
| Debian 11 (bullseye), Ubuntu 20.04 | v60.5 |
| Debian 10 (buster) | v56.5 |

### Installing a specific version

```
curl -L https://raw.githubusercontent.com/Rovart/enhanced-mailinabox/<VERSION>/setup/bootstrap.sh | sudo bash
```

Where `<VERSION>` is a release tag, such as `v60.5`. Downgrading is not supported and may not work.

## Command line

Most settings are also available from the command line, with `management/cli.py` in the box's copy of the code (`/root/mailinabox` when installed with the command above). For example:

```
sudo /root/mailinabox/management/cli.py system services
sudo /root/mailinabox/management/cli.py system services disable nextcloud
sudo /root/mailinabox/management/cli.py system web-access webmail local
```

Run `management/cli.py` without arguments to see every command. If local-only access locks you out of the control panel, `sudo /root/mailinabox/management/cli.py system web-access all public` makes everything reachable again.

## Development

The [Vagrantfile](Vagrantfile) starts a virtual machine for each supported operating system (`bookworm`, `trixie`, `jammy` and `noble`) and installs the box from your working copy:

```
vagrant up --provision trixie
```

Contributions are welcome as pull requests: see [CONTRIBUTING.md](CONTRIBUTING.md).

## Support and security

Report bugs and request features in the [issues](https://github.com/Rovart/enhanced-mailinabox/issues). Security vulnerabilities must be reported privately instead: see the [security guide](security.md), which also describes the box's security posture.

## Credits and license

Enhanced Mail-in-a-Box is maintained by [Rovart](https://github.com/Rovart). It is a fork of [Power Mail-in-a-Box](https://github.com/ddavness/power-mailinabox), created by [David Duque](https://github.com/ddavness) as a fork of [Mail-in-a-Box](https://github.com/mail-in-a-box/mailinabox), by Joshua Tauberer and its contributors. All three are released into the public domain under [CC0 1.0](LICENSE).
