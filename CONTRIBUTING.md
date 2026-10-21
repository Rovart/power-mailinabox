# Contributing

Power Mail-in-a-Box is an open source project, and contributions are welcome: bug reports, fixes, tests and new features.

## Reporting bugs

Open an [issue](https://github.com/Rovart/power-mailinabox/issues) with the version you're running (shown in the status checks), the operating system and what you see (the setup's output, or the error in the control panel).

Security vulnerabilities must not be reported in public issues: see the [security guide](security.md#reporting-security-vulnerabilities).

## Development

Clone the repository:

    $ git clone https://github.com/Rovart/power-mailinabox

### Vagrant

We recommend [Vagrant](https://developer.hashicorp.com/vagrant/install) with VirtualBox or libvirt. The [Vagrantfile](Vagrantfile) defines a virtual machine for each supported operating system, and installs the box in it from your working copy:

| Machine | Operating system | Address |
| --- | --- | --- |
| `bookworm` | Debian 12 | 192.168.168.2 |
| `trixie` | Debian 13 | 192.168.168.3 |
| `jammy` | Ubuntu 22.04 | 192.168.168.4 |
| `noble` | Ubuntu 24.04 | 192.168.168.5 |

Start one (or several) of them with:

    $ vagrant up --provision trixie

The setup runs without asking questions and skips the network checks, so it doesn't matter if your IP address is listed in the Spamhaus block lists (most residential addresses are).

To use the web applications, add the machine's hostname to your `hosts` file:

    $ echo "192.168.168.3 trixie.mailinabox.lan" | sudo tee -a /etc/hosts

The control panel is then at https://trixie.mailinabox.lan/admin, with an administrator `me@trixie.mailinabox.lan` whose password is `12345678`.

### Making changes

Your working copy is mounted inside the virtual machines at `/vagrant`, so your changes appear there right away. Running `vagrant up --provision <machine>` again repeats the setup with them.

You can also log into a machine and re-run parts of the setup:

    $ vagrant ssh trixie
    vm$ cd /vagrant
    vm$ sudo setup/nextcloud.sh  # replace with the script you'd like to re-run

Please test changes to the setup on every operating system they affect.

### Tests

The `tests` directory has scripts that check a running box from the outside, for example:

    $ python3 tests/test_mail.py trixie.mailinabox.lan me@trixie.mailinabox.lan 12345678

More tests are welcome.

### Pull requests

- Keep each pull request focused on one change, and explain what it changes and why.
- Follow the existing code style ([.editorconfig](.editorconfig): tabs for indentation in the shell and Python scripts).
- Describe user-visible changes in the [changelog](CHANGELOG.md), under the next version.

## Public domain

This project is in the public domain. Copyright and related rights in the work worldwide are waived through the [CC0 1.0 Universal public domain dedication][CC0]. See the LICENSE file in this directory.

All contributions to this project must be released under the same CC0 waiver. By submitting a pull request or patch, you are agreeing to comply with this waiver of copyright interest.

[CC0]: http://creativecommons.org/publicdomain/zero/1.0/

## Code of Conduct

This project has a [Code of Conduct](CODE_OF_CONDUCT.md). Please review it when joining our community.
