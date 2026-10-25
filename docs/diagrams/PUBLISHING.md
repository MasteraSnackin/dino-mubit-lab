# Live system map

The interactive map is published at https://masterasnackin.github.io/dino-mubit-lab/.

GitHub Pages serves the root of the dedicated `gh-pages` branch. It contains the standalone map and its supporting source, receipt and notices. The local Python application and private runtime records are not part of this site.

## Published files

| Pages file | Source |
| --- | --- |
| `index.html` | `docs/diagrams/dino-mubit-lab.html` on `main`, copied without modification |
| `dino-mubit-lab.architecture.json` | Editable map source from the same revision |
| `dino-mubit-lab.delivery.json` | Original validation and delivery receipt |
| `.nojekyll` | Empty file that tells Pages to serve the prepared static files |
| `ARCHIFY-LICENSE.txt` | Archify MIT licence |
| `THIRD_PARTY_NOTICES.md` | Archify's bundled third-party notices |

The initial publication uses map files from commit `b505e60bfd2747e6dad53e8bf70e8d3d06188bd3`. The map's architecture evidence remains pinned to the source revision recorded inside its original receipt. Hosting the file does not change those claims.

## Updating the site

Validate and deliver any map revision before publishing it. Copy that checked HTML to `index.html` on `gh-pages`, together with the corresponding editable JSON and receipt. Preserve the licence notices and `.nojekyll`, then commit and push the branch normally. GitHub Pages deploys changes pushed to that branch; edits to map files on `main` do not automatically update the hosted copy.

After deployment, compare the public HTML checksum with the delivered artifact and check its search, focus, guided views and theme controls in a browser. Update the publication receipt after those checks. The README should continue to link to the live URL rather than the GitHub `/blob/` file viewer.
