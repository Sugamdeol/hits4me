# FeelingSurf Viewer on SnapDeploy

This folder is a standalone **FeelingSurf Viewer** deployment for
[SnapDeploy](https://snapdeploy.dev/). It does not run or include the 9Hits
viewer from the repository root.

It extends the official multi-architecture image,
[`feelingsurf/viewer:stable`](https://hub.docker.com/r/feelingsurf/viewer), and
exposes the viewer's built-in health endpoint on port `3000` (or the port in
`PORT`).

## Deploy

1. In SnapDeploy, create a container from this GitHub repository.
2. Enable monorepo/root-directory selection and set the root directory to
   **`snapdeploy`**. SnapDeploy will detect `snapdeploy/Dockerfile` there.
3. Add this secret environment variable:

   | Name | Value |
   | --- | --- |
   | `access_token` | Your private FeelingSurf access token |

   `ACCESS_TOKEN` (upper-case) is also accepted as a convenience. Do not set
   both; `access_token` takes precedence.
4. Set the container/internal port to **`3000`** if SnapDeploy asks for it.
   The included Dockerfile already exposes that port.
5. Deploy. The public URL and `/` endpoint are used as the container health
   endpoint; the viewer itself runs headlessly in the background.

> **Keep the token secret.** A FeelingSurf access token grants access to your
> account. Add it through SnapDeploy's secret/environment-variable settings,
> never in the Dockerfile or Git history.

## Resources

FeelingSurf recommends approximately **2 GB RAM and 2 CPU cores per viewer**.
A smaller plan may be terminated for exceeding its memory limit. Run only one
viewer per SnapDeploy container.

SnapDeploy may put free containers to sleep when they receive no traffic. If
continuous surfing is required, use an always-on plan; repeatedly pinging a
free service may violate the hosting plan's intended usage.

## Local test

```bash
cd snapdeploy
cp .env.example .env
# Put your real token in .env, then:
docker compose up --build
```

In another terminal:

```bash
curl http://localhost:3000/
```

Stop and remove the test container with `docker compose down`.

## Environment variables

| Variable | Required | Default | Description |
| --- | --- | --- | --- |
| `access_token` | Yes | — | Official FeelingSurf authentication variable |
| `ACCESS_TOKEN` | Alternative | — | Alias used only when `access_token` is unset |
| `PORT` | No | `3000` | HTTP health endpoint/container port |
