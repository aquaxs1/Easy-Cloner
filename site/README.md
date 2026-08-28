# 🌐 Easy Server Cloner — Website

The marketing and documentation site for the bot. Plain HTML, CSS and JavaScript —
no framework, no build step, no dependencies.

## Deploying to Vercel

### Through the dashboard

1. [vercel.com/new](https://vercel.com/new) → import `aquaxs1/Easy-Cloner`
2. **Root Directory** → set to `site`
3. **Framework Preset** → `Other`
4. Leave build command and output directory empty — there is nothing to build
5. **Deploy**

### From the terminal

```bash
npm i -g vercel
cd site
vercel          # preview deployment
vercel --prod   # production
```

> The **Root Directory** setting is the one that matters. Point it at `site`, or
> Vercel will look at the Python project in the repository root and find no site.

## Running it locally

Any static file server will do:

```bash
cd site
python3 -m http.server 8000
```

Then open <http://localhost:8000>. Links use absolute paths (`/features.html`), so
opening the files directly via `file://` will break navigation — use a server.

## Pages

| File | Purpose |
|---|---|
| `index.html` | Landing page, hero, click-through slideshow, highlights |
| `setup.html` | Nine-step setup guide with copyable commands |
| `features.html` | All commands, options, limits, configuration |
| `faq.html` | Questions and troubleshooting, as an accordion |
| `terms.html` | Terms of use and privacy |
| `404.html` | Not-found page (Vercel serves this automatically) |

## Structure

```
site/
├── index.html · setup.html · features.html · faq.html · terms.html · 404.html
├── assets/
│   ├── css/style.css        One stylesheet, tokens at the top
│   ├── js/main.js           Slideshow, copy buttons, reveals, pixel canvas
│   └── img/
│       ├── logo.svg         The mark, rebuilt as vector — used everywhere
│       ├── logo.png         512px raster, for the Apple touch icon
│       └── og-image.png     1200×630 social preview card
├── vercel.json              Security headers, asset caching, short redirects
└── robots.txt
```

## Notes

- **The logo** is `assets/img/logo.svg`, a vector rebuild of the original artwork so
  it stays crisp at any size and needs no dark background baked in.
- **Colours and spacing** are CSS custom properties at the top of `style.css`. Change
  `--brand` there and the whole site follows.
- **JavaScript is optional.** Without it the slideshow shows its first step, the
  accordion stays closed and everything else works normally.
- **`prefers-reduced-motion`** disables the pixel canvas and every animation.
- **No cookies, no analytics, no trackers.** The only external request is Google Fonts.

## Editing content

The nav and footer are duplicated across pages on purpose — it keeps the site
dependency-free and renders without JavaScript. When you change a footer link,
update it in all six files.

## The Content-Security-Policy and the inline script

`vercel.json` sends a strict CSP. `script-src` allows `'self'` plus one SHA-256
hash, which covers the single inline one-liner in every page's `<head>`:

```html
<script>document.documentElement.classList.add("js");</script>
```

It has to stay inline — it runs before the first paint so the no-JS styles never
flash — so a hash is used instead of `'unsafe-inline'`. **Change that line by even
one character and the browser silently refuses to run it**, and the `js` class
never lands on `<html>`. If you edit it, recompute the hash and update the
`Content-Security-Policy` value in `vercel.json`:

```sh
printf '%s' 'document.documentElement.classList.add("js");' \
  | openssl dgst -sha256 -binary | openssl base64
```

Inline `<style>` and `style="…"` attributes are covered by `'unsafe-inline'` in
`style-src`, so those need no such care.
