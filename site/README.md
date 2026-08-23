# 🌐 Easy Server Cloner — Website

The marketing and documentation site for the bot. Plain HTML, CSS and JavaScript —
no framework, no build step, no dependencies.

## Deploying to Vercel

### Through the dashboard

1. [vercel.com/new](https://vercel.com/new) → import `aquaxs1/Easy-Cloner`
2. **Leave Root Directory at its default** (`Easy-Cloner (root)`)
3. **Deploy**

That is the whole procedure. The `vercel.json` in the repository root tells Vercel
there is nothing to build and that `site/` is the directory to serve.

> **Do not go looking for `site` in the Root Directory picker — it will not be
> there.** Vercel only lists subdirectories that contain a project manifest it
> recognises (`package.json`, `next.config.js`, and friends). This site is plain
> HTML with no manifest, so the picker shows only the repository root. That is
> expected, and the root `vercel.json` is what makes it work anyway.

### From the terminal

```bash
npm i -g vercel
vercel          # preview deployment — run from the repository root
vercel --prod   # production
```

### Two config files, on purpose

| File | Applies when |
|---|---|
| `vercel.json` (repo root) | Root Directory is the default — **the documented path** |
| `site/vercel.json` | Root Directory was manually set to `site` |

Both carry the same headers and redirects, so either setup produces the same
result. If you change one, change the other too.

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
