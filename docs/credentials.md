# Where do I put my Benchling API key?

SampleTrace supports four credential sources. Pick one per environment.
**Never commit a key to source control** — `.gitignore` blocks the common
ones but you are ultimately the one running `git add`.

## Precedence (first hit wins)

```
1. BENCHLING_API_KEY environment variable    ← preferred in CI / containers
2. OS keyring (sampletrace.benchling)        ← preferred on developer laptops
3. .env file at the current working directory ← only if python-dotenv is installed
4. api_key: in your YAML config              ← discouraged; emits a WARNING
```

The runtime logs which source the key came from (never the key itself).
Verify with:

```bash
sampletrace verify-auth --benchling-config bch.yml --dry-run
```

Output:

```
tenant URL    : https://acme.benchling.com
schema_id     : ts_xxxxxxxxxxxx
key source    : env
key (redacted): ***abcd
[dry-run] not contacting Benchling
```

If you want to actually hit Benchling, drop `--dry-run`:

```bash
sampletrace verify-auth --benchling-config bch.yml
```

One paginated `custom_entities.list(page_size=1)` call — no sample data is
pulled into the report or printed.

---

## Recipe 1 — Developer laptop (recommended)

Store the key in your OS keyring; never have it in any file:

```bash
sampletrace configure --tenant-url https://acme.benchling.com
# Benchling API key:          (hidden input)
# Repeat for confirmation:    (hidden input)
# wrote config template -> bch.yml
```

The key goes to:

- **macOS**: Keychain (item type: generic password, service `sampletrace.benchling`)
- **Windows**: Windows Credential Manager (Generic Credentials)
- **Linux**: Secret Service (GNOME Keyring / KWallet)
- **Linux headless**: install `keyrings.alt` for a file-based fallback,
  or just use `BENCHLING_API_KEY`.

To check which backend is in use:

```bash
sampletrace keyring-info
```

To remove the stored key:

```bash
sampletrace configure --tenant-url https://acme.benchling.com --delete
```

---

## Recipe 2 — Local `.env` file (alternative for laptops)

If you prefer a file-based workflow but don't want the key in the YAML
config that goes into git, drop it in `.env`:

```bash
# .env  (gitignored by default)
BENCHLING_API_KEY=sk_your_key_here
```

Then run normally — `python-dotenv` (installed with the `[benchling]` extras)
auto-loads `.env` from the CWD at CLI startup.

Add `.env.example` to git for the next person:

```bash
# .env.example
BENCHLING_API_KEY=sk_paste_yours_here
```

Our `.gitignore` permits `.env.example` and blocks `.env` / `.env.*`.

---

## Recipe 3 — GitHub Actions CI

Store the key as a repository or org secret, expose it as an env var:

```yaml
- name: Reconcile
  env:
    BENCHLING_API_KEY: ${{ secrets.BENCHLING_API_KEY }}
  run: |
    sampletrace verify-auth --benchling-config config/bch.yml
    sampletrace reconcile -b config/bch.yml -s SampleSheet.csv -o reports/ --fail-on-flagged
```

The YAML config in your repo should have `api_key: null` (the env var wins
anyway, and explicit null is documentation for the next reader).

---

## Recipe 4 — Docker / docker-compose

Docker is the one place you should *not* bake the key into the image. Two
acceptable patterns:

### 4a. Pass via environment

```yaml
# docker-compose.yml
services:
  reconcile:
    image: sampletrace:latest
    environment:
      BENCHLING_API_KEY: ${BENCHLING_API_KEY}      # from your shell env
    volumes:
      - ./config:/config:ro
    command: reconcile -b /config/bch.yml -s ... -o ...
```

Run with:

```bash
export BENCHLING_API_KEY="sk_..."
docker compose run --rm reconcile
```

### 4b. Docker swarm / compose secrets

```yaml
# docker-compose.yml
services:
  reconcile:
    image: sampletrace:latest
    secrets:
      - benchling_api_key
    entrypoint:
      - sh
      - -c
      - 'export BENCHLING_API_KEY="$$(cat /run/secrets/benchling_api_key)" && sampletrace "$$@"'
      - --
    command: reconcile -b /config/bch.yml -s /data/SampleSheet.csv -o /out

secrets:
  benchling_api_key:
    file: ./secrets/benchling_api_key.txt   # NEVER commit this file
```

The secret is mounted as a file at `/run/secrets/benchling_api_key`, read
into the env var inside the container, and `sampletrace` picks it up via
the standard precedence. The key never appears in `docker inspect`, in the
image, or in container metadata.

---

## Recipe 5 — Kubernetes

```yaml
apiVersion: v1
kind: Secret
metadata:
  name: benchling-api-key
type: Opaque
stringData:
  BENCHLING_API_KEY: "sk_..."   # use `kubectl create secret` so this isn't in YAML you commit
---
apiVersion: batch/v1
kind: CronJob
metadata:
  name: nightly-reconcile
spec:
  schedule: "0 6 * * *"
  jobTemplate:
    spec:
      template:
        spec:
          restartPolicy: Never
          containers:
          - name: sampletrace
            image: sampletrace:latest
            envFrom:
              - secretRef:
                  name: benchling-api-key
            args: ["reconcile", "-b", "/config/bch.yml", "-s", "...", "-o", "...", "--fail-on-flagged"]
```

Even better: pair with [external-secrets](https://external-secrets.io/) or
[Sealed Secrets](https://github.com/bitnami-labs/sealed-secrets) so the raw
key never enters your cluster YAML at all.

---

## What NOT to do

| Antipattern | Why it's bad | Do this instead |
|---|---|---|
| `benchling.api_key: sk_...` in `bch.yml`, then commit | Key is in git history forever; tool emits warning | `sampletrace configure` or env var |
| `export BENCHLING_API_KEY=$(cat ~/.bch_key)` in `.bashrc` | Key is in plaintext shell history + a file | OS keyring |
| Pasting the key into a Slack channel for a teammate | Now it's indexed and in incident response surface | Rotate it; share via 1Password / vault |
| Embedding the key in a Docker image at build time | Key is in every layer + image registry | Mount as secret or env at run time |
| Reading the key into a Python variable and logging it | Shows up in logs/Sentry/grep | We never do this; redact yourself if you fork |
| Skipping `verify-auth` and discovering the bad key during a 4-hour run | Loses the run's whole window | Add `verify-auth` as the first CI step |

---

## What SampleTrace promises about the key

- **Never logged.** Even with `-v/--verbose`. Only the source name (`env`,
  `keyring`, ...) and a `***abcd` redaction appear in logs.
- **Never written to any output file.** Inspect `sample_provenance.json`
  after a run if you want to confirm.
- **Never echoed back from `configure`.** Input is hidden + confirmed.
- **Never sent anywhere except the configured `tenant_url`.** The
  benchling-sdk is the only HTTP client used.

## Rotating a key

1. Generate a new key in Benchling.
2. Update your secret store (`sampletrace configure --tenant-url ...` again
   overwrites the keyring entry; `kubectl create secret ... --dry-run -o yaml | kubectl apply -f -`
   for k8s; etc.).
3. Run `sampletrace verify-auth` to confirm.
4. Delete the old key in Benchling.
