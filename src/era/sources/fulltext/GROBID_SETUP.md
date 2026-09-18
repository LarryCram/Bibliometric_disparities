# Setting up Docker + GROBID from scratch

Notes from actually getting this working on this machine (Ubuntu, Docker
installed via the `docker` snap package) - written so a similar machine can
be set up without repeating the same troubleshooting.

## Why Docker, not building GROBID from source

GROBID's source repo (`kermitt2/grobid` on GitHub) can in principle be
built with `./gradlew run`, but this machine only had JRE 8 and JRE 21
installed - no JDK at all (no `javac`), and even after that's fixed, the
repo's pinned Gradle wrapper version matters: Gradle 7.2 (what an older
checkout uses) cannot run under JDK 21 at all ("Unsupported class file
major version 65"). GROBID's `0.9.0` tag bumps the Gradle wrapper to 9.0.0,
which does support JDK 21 - but by that point Docker is just simpler.
**Use Docker unless you have a specific reason to build from source.**

## 1. Install Docker

On Ubuntu, either the `docker` snap or the standard `docker.io`/Docker CE
apt package works. This machine uses the snap:

```
sudo snap install docker
```

## 2. Fix snap Docker's permissions (if `docker ps` fails with "permission denied")

Unlike `apt`'s `docker.io`/`docker-ce` packages, the `docker` **snap**
package does **not** automatically create a `docker` group or grant your
user access to the daemon socket. If `docker ps` fails with:

```
permission denied while trying to connect to the docker API at unix:///var/run/docker.sock
```

do this:

```
sudo groupadd docker
sudo usermod -aG docker $USER
sudo snap restart docker
```

Then **fully log out and back in** (a new group membership needs a fresh
login session - `newgrp docker` in an existing terminal is not reliably
enough for socket access) or, if that still doesn't work, **reboot**. The
socket's group ownership is only set correctly when `dockerd` itself
(re)starts *after* the `docker` group exists - if you create the group
while `dockerd` is already running, you must restart the `docker` snap
service (or reboot) for the socket to actually pick up the new group,
even though the group and your membership in it both already exist.

Verify with:
```
docker ps
```
should return an empty table (no permission error) once this is done.

## 3. Pull and run GROBID

```
docker pull grobid/grobid:0.9.0
docker run -d --restart unless-stopped -p 8070:8070 --name grobid grobid/grobid:0.9.0
```

`--restart unless-stopped` is recommended over `--rm`: this container has
crashed under sustained processing load at least once on this project (see
Troubleshooting below), and a plain `--rm` container does not come back on
its own after a crash or a machine reboot - `unless-stopped` does, without
needing to be manually re-launched every time.

Verify it's up:
```
curl -s http://localhost:8070/api/isalive
```
should print `true`. Check the version:
```
curl -s http://localhost:8070/api/version
```

## 4. Point the pipeline at it

`run_grobid.py` talks to GROBID over HTTP at `http://localhost:8070` - no
further configuration needed once the container is running. See
`commands.txt` in this directory for copy-pasteable snippets that check
current process/container state and (re)launch both the downloader and
`run_grobid.py`.

## Troubleshooting

**GROBID container silently disappears / stops responding after a while.**
Observed during a long run processing ~10,000 PDFs: after several hours of
sustained 8-way-concurrent load, GROBID started returning `500 Server
Error` for `processFulltextDocument` requests, and the container was gone
entirely (`docker ps -a` showed nothing) shortly after. Likely cause is
memory exhaustion inside the container under sustained load - GROBID is a
JVM service with substantial default heap usage per concurrent request.
Mitigations, in order of effort:
- Use `--restart unless-stopped` (see above) so it comes back automatically
  rather than needing a manual `docker run` every time.
- Lower `CONCURRENCY` in `run_grobid.py` (currently 8) if 500 errors recur
  - fewer concurrent GROBID requests means lower peak memory.
- Give the container an explicit memory limit and check `docker logs
  grobid` for OOM kills if it keeps happening:
  `docker run -d --restart unless-stopped -p 8070:8070 --memory=8g --name grobid grobid/grobid:0.9.0`

**"permission denied" on `docker.sock` even after adding yourself to the
`docker` group.** You created the group *after* `dockerd` was already
running and didn't restart the daemon (see step 2) - `sudo snap restart
docker`, then log out/in.

**Everything (downloader, GROBID, run_grobid.py) is just gone after
walking away for a while.** This machine has crashed/rebooted more than
once during this project. None of these processes survive a reboot on
their own (Docker container needs `--restart unless-stopped`, per above;
the Python scripts need to be manually relaunched via `commands.txt` -
`nohup`/`disown` only protects against the *terminal* closing, not a
reboot). Check `docker ps -a` and `ps aux | grep -E
"download_oa_pdfs|run_grobid"` after any suspected crash/reboot before
assuming things are still running.
