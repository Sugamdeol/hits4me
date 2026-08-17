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
3. Set the container/internal port to **`3000`** if SnapDeploy asks for it.
   The included Dockerfile already exposes that port.
4. Deploy. The public URL and `/` endpoint are used as the container health
   endpoint; the viewer itself runs headlessly in the background.

The requested FeelingSurf access token is embedded in the image configuration,
so no token variable is required in SnapDeploy. You can override it at runtime
by setting `access_token` in SnapDeploy's environment settings.

> **Security warning:** The embedded token is visible to anyone who can read
> this repository or inspect the built image. Rotate the token in FeelingSurf
> and switch to a SnapDeploy secret if the repository or image is shared.

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
docker compose up --build
```

The embedded token is used by default. To test with a different token:

```bash
ACCESS_TOKEN=another_token docker compose up --build
```

In another terminal:

```bash
curl http://localhost:3000/
```

Stop and remove the test container with `docker compose down`.

## Environment variables

| Variable | Required | Default | Description |
| --- | --- | --- | --- |
| `access_token` | No | Embedded token | Official FeelingSurf authentication variable; overrides the image default |
| `ACCESS_TOKEN` | No | — | Convenience override used by the local Compose file |
| `PORT` | No | `3000` | HTTP health endpoint/container port |
