# sitecheck

A static checker for static sites. One Python file, no dependencies, no config.

If you build sites the plain way — HTML, ES modules, no bundler — nothing is
watching for the small mistakes a build step would have caught. `sitecheck`
watches for them.

```
$ sitecheck.py example

duplicate id (1)
  index.html  #title

function never called (1)
  index.html#0  neverCalled()

id referenced but missing (1)
  index.html  #nope

import does not resolve (1)
  index.html#0  unused is not exported by /app.js

internal link goes nowhere (1)
  index.html  /missing

leftover (1)
  index.html#0  console.log left in

unused import (1)
  index.html#0  unused from /app.js

7 issues — 2 scripts, 1 page
```

## What it finds

| Check | Why it matters |
| --- | --- |
| JavaScript that does not parse | a typo in an inline module fails silently in the browser console |
| imports that do not resolve | renaming an export leaves every importer broken |
| imports nothing uses | dead weight, and a sign a refactor was left half-done |
| ids referenced but missing | `$('#thing')` returning null is the most common runtime error in a plain site |
| the same id twice on a page | the second element is unreachable |
| functions never called | usually the remains of a rewrite |
| internal links to pages that do not exist | a 404 nobody clicked yet |
| `console.log`, `TODO`, `FIXME`, `debugger` | things that were not meant to ship |

## Install

There is nothing to install. Copy the file:

```bash
curl -O https://raw.githubusercontent.com/MKivrak/sitecheck/main/sitecheck.py
python sitecheck.py
```

Python 3.9 or newer. The JavaScript syntax check uses `node` if it is on your
PATH and is skipped quietly if it is not, so the other checks still run
anywhere.

## Usage

```bash
sitecheck.py              # check the current folder
sitecheck.py public/      # check somewhere else
sitecheck.py --quiet      # print nothing when clean, for CI
sitecheck.py --no-node    # skip the syntax check even if node is available
```

Exit code is `0` when clean and `1` when anything was found, so it drops
straight into CI:

```yaml
- run: python sitecheck.py --quiet
```

## What it assumes

- pages are `*.html` in the root of the folder you point it at
- modules are `*.js` anywhere below it, `node_modules` excluded
- a link like `/about` is expected to resolve to `about.html`, which is how
  clean-URL hosts serve static sites
- `$('#id')`, `getElementById('id')` and `querySelector('#id')` are how you
  reach elements

It does not parse HTML or JavaScript properly — it reads them with regular
expressions. That makes it fast, dependency-free and occasionally wrong. A
false positive is a nuisance, not a failure, and `--no-node` plus a glance at
the line is usually enough to settle it.

## Why it exists

I wrote it for a site with nine pages and a dozen modules and no build step. It
caught a dead function that still referenced a deleted element, three imports
left behind by a refactor, and a link to a page I had renamed. All of them would
have shipped.

## Licence

MIT
