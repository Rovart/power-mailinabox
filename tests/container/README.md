# Container tests

These scripts install Power Mail-in-a-Box in Docker containers running systemd, one
per supported operating system, and check that everything works: sending and
receiving mail, IMAP/POP/SMTP logins, spam learning, quotas, webmail, Nextcloud
(CardDAV/CalDAV), Z-Push (ActiveSync), the control panel and its API, backups, the
status checks, turning the optional services off and on, and restricting the web
applications to local access.

Run them before releasing a new version (the quick checks in `tests/static-checks.sh`
are enough for everyday changes):

```
tests/container/run-all.sh                     # fresh installs on every OS
tests/container/run-all.sh debian-13           # only one OS
tests/container/run-all.sh -u origin/main      # upgrade from main to this copy
```

It needs Docker (it starts `dockerd` if it isn't running) and about 5 GB of disk
per operating system, and takes 20 to 40 minutes. It prints a summary table and
exits with status 1 if anything failed; the logs are in `/tmp/pmiab-tests`. The
containers (`pmiab-test-<os>`) are kept until the next run, so you can look inside
them with `docker exec -it pmiab-test-debian-13 bash`.

The box is set up as `box.example.com` with the user `me@example.com` and password
`Secret123!pw`. `patch-for-container.sh` makes a few test-only changes to the copy
in the container (no IPv6, Docker's `/etc/resolv.conf`) — never to the repository.

| Script | What it does |
|---|---|
| `run-all.sh` | Everything below, in parallel for each OS, and a summary |
| `build-images.sh` | Builds the `pmiab-<os>` images (systemd, plus the CA certificates of an HTTPS proxy if there is one) |
| `run-setup.sh` | Copies a directory into a container and runs the setup in it |
| `verify.sh` | Functional checks, run inside the container |
| `services-test.sh` | Optional services off/on, run inside the container |
| `access-test.sh` | Local-only access, run from the host (whose requests are "external") |
