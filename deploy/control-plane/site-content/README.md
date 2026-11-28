# Site content

The control plane mounts `AGENT_SITE_CONTENT_DIR` read-only at
`/etc/cloud-agent/site`. This directory is the default and configures nothing:
without `site.yaml` the footer hides the Team and source links, and without
`terms.<locale>.txt` the Terms of Service come from Admin settings. A
deployment keeps its own directory in its own repository, with real member
names and photos, and points `AGENT_SITE_CONTENT_DIR` at a copy on the
control-plane host. The file formats are in `ENVIRONMENT.md`, "Site content
files".

A member may also have a `photo_url`: an absolute HTTPS URL, or an inline
`data:image/jpeg;base64,...` URL (PNG and WebP work too) for a photo whose
site refuses embedding. The whole `site.yaml` may be at most 256 KiB. The
browser loads the photo without a referrer, and shows the member's initials if
it is missing or fails to load.

```yaml
members:
  - name: Ada Lovelace
    photo_url: https://example.org/team/ada.jpg
```
