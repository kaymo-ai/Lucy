# Lucy — camp install page

A single static page. `index.html` is self-contained (inline CSS, system fonts, no
scripts, no CDN, no external requests of any kind) and the only other files are the
PNGs in `screens/`. Serve the directory with anything — `cd site && python3 -m http.server 8000`
to check it locally, or copy `site/` onto the box and point a web server at it.
There is deliberately **no JavaScript password prompt**: a password checked in the
browser on a file the browser already downloaded protects nothing. The gate is
whatever serves the files, so pick one of the two below. Worth doing before you send
the link round — the People screenshot is a real campmate's profile.

## Option A — Caddy, on the camp VM

Add the site block to the Caddyfile (`/etc/caddy/Caddyfile`), then reload with
`sudo systemctl reload caddy`:

```
lucy.marcusfoster.com {
    root * /var/www/lucy
    file_server
    basic_auth {
        camp $2a$14$REPLACE_WITH_THE_HASH_BELOW
    }
}
```

Generate the hash — it is bcrypt, and the `$` signs must be pasted verbatim:

```bash
caddy hash-password --plaintext 'the-camp-password'
```

`basic_auth` is the Caddy 2.8+ spelling. On an older Caddy the directive is
`basicauth` with the same block body — `caddy version` settles which one you need.
Caddy's own directive ordering puts the auth ahead of `file_server`, so the order
inside the block does not matter.

## Option B — Hostinger, `.htaccess` + `.htpasswd`

Put `.htaccess` in the directory being served:

```apache
AuthType Basic
AuthName "Preservation Society"
AuthUserFile /home/USERNAME/.htpasswd
Require valid-user
```

`AuthUserFile` must be an **absolute filesystem path**, and the password file must
live *outside* the web root (`/home/USERNAME/`, not `public_html/`) or Apache will
happily serve it to anyone who guesses the URL. Create it over SSH:

```bash
htpasswd -c -B /home/USERNAME/.htpasswd camp
```

`-B` forces bcrypt, `-c` **creates and overwrites** — use it for the first user only
and drop the `-c` to add anyone else. Without shell access, Hostinger's hPanel has a
"Password Protect Directories" tool that writes both files for you.

## Check it actually locked

Whichever route you took, confirm it rather than assuming it — a typo'd
`AuthUserFile` path and a mis-spelled Caddy directive can both fail *open*:

```bash
curl -I https://lucy.marcusfoster.com/
```

Want `HTTP/2 401`. A `200` means the page is serving to the world.

## Filling in the placeholders

Two dashed orange boxes in `index.html` are marked **FOR MARCUS** and need real
content before the link goes out: the TestFlight invite link, and how to report
problems. Search the file for `todo` to find them.
